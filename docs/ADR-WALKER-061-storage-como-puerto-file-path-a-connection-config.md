# ADR-WALKER-061: Storage como puerto — de file-path a connection-config

> **Status:** Ejecutado
> **Fecha:** 2026-08-25
> **Implementado:** 2026-08-26 (F1–F6 + config SSOT + eliminación workflow.v3)
> **Autor:** Walker (agente) + PO
> **Complementa:** ADR-WALKER-045 (composition root único), ADR-WALKER-054 (almacenamiento cognitivo unificado), ADR-WALKER-055 (eliminación kg_search), ADR-WALKER-060 (NestJS-inspired org patterns)
> **Hipótesis origen:** "las memorias no deben conocer infraestructura; la conexión a BD es configuración" (modelo empresarial: conexión = config inyectada, no path hardcodeado)

---

## 1. Problema

Hoy CPG/CKG/Delta/Trace se persisten en archivos `.json.gz` bajo `.cognitive/projects/<pid>/data/`. La abstracción `CognitiveGraph` ya existe y los repos la usan — PERO la **forma de la abstracción es de filesystem**, no de conexión. Esto impide migrar a BD sin reescribir los consumidores.

### 1.1 Anti-patrones identificados (evidencia)

| # | Anti-patrón | Evidencia | Impacto para migrar a BD |
|---|---|---|---|
| AP-1 | Ruta de storage DENTRO del schema del índice | `libs/cognitive_graph/cognitive_registry.py:59,79,93,136` (`"storage": {"path": "ckg.json.gz", "format": "json.gz"}`); repetido en `cognitive_ckg_repo.py:51` | Schema (fields/types) acoplado a archivo físico; no separable de conexión |
| AP-2 | La "conexión" es un directorio | `shared/core/cognitive_graph/__init__.py:34` (`__init__(self, chain_dir: str)`); `features/main/composition/registry.py:328` (`CognitiveGraph(memory_data_dir(chain_dir))`) | Root de BD = path; BD necesita connection config, no path |
| AP-3 | Factory de forma path | `graph_factory: Callable[[str], Any]` en `cognitive_delta_repo.py:33`, `cognitive_ckg_repo.py:33`, `repository.py:176`, `trace_push.py:40` | Firma asume un solo string = path; BD necesita objeto config |
| AP-4 | CPG deriva root mutando string de archivo | `cognitive_cpg_repo.py:34-37` (`CognitiveGraph(self._file_repo.cpg_path.replace("cpg.json.gz", ""), ...)`) | Repo conoce `.json.gz` y lo pela; fuga de infra |
| AP-5 | CPG mezcla dos backends | `cognitive_cpg_repo.py:13,19-20,42-46` (load/save vía `CpgFileRepository`, search vía `CognitiveGraph`) | No hay un único punto de swap |
| AP-6 | `CpgFileRepository` importado concretamente | `cognitive_cpg_repo.py:13` | Sin puerto `StorageBackend`; dependencia dura a archivo |
| AP-7 | No existe puerto `StorageBackend` | `CognitiveGraph` hace IO interno vía `Index` (lee/escribe `.json.gz`) | Migrar = reescribir internos de `CognitiveGraph`, no cambiar adapter |
| AP-8 | Identidad del store = path | `cognitive_delta_repo.py:33`, `cognitive_ckg_repo.py:33`, `cognitive_cpg_repo.py:19` reciben `project_root`/`data_dir`/`chain_dir` | Enterprise: identidad = conexión+db+collection por config |
| AP-9 | Doble fuente de verdad del storage | `shared/core/cognitive_graph/__init__.py:74` hardcodea `"storage": {"path": "data/ckg.json.gz"}` aparte de `cognitive_registry.py` | Dos lugares declaran el path del archivo |

### 1.2 Consecuencia

Mover a BD hoy requeriría tocar `CognitiveGraph`, `Index`, `cognitive_registry.py`, `cognitive_cpg_repo.py`, y cambiar la firma de `graph_factory` en 4+ call sites — **filtrando infraestructura a los repos**. Viola el principio de la hipótesis: *la memoria no debe conocer infraestructura*.

---

## 2. Decisión

Introducir **`StorageBackend` como puerto** y **`MemoryConnection` como config**, separando schema de conexión.

### D1 — `MemoryConnection` (config, no path)

```python
# shared/core/cognitive_graph/connection.py

@dataclass(frozen=True)
class MemoryConnection:
    backend: str          # "file" | "postgres" | "mongo" (extensible)
    uri: str              # file://./data  |  postgresql://user@host/db
    database: str
    collection_prefix: str = ""
    # creds desde env/secret manager — NUNCA en código
```

### D2 — `StorageBackend` (Protocol/port)

```python
# shared/core/cognitive_graph/ports.py

class StorageBackend(Protocol):
    """Puerto de almacenamiento. FileBackend hoy; DbBackend mañana."""
    def store(self, index: str, doc_id: str, doc: dict) -> None: ...
    def get(self, index: str, doc_id: str) -> dict | None: ...
    def search(self, index: str, query: dict) -> dict: ...
    def delete(self, index: str, doc_id: str) -> None: ...
    def store_many(self, index: str, docs: dict[str, dict]) -> None: ...
    def delete_many(self, index: str, ids: set[str]) -> None: ...
    def max_key(self, index: str, prefix: str) -> tuple[str, int] | None: ...
```

### D3 — `CognitiveGraph` recibe backend + connection (NO chain_dir)

```python
# shared/core/cognitive_graph/__init__.py

class CognitiveGraph:
    def __init__(self, backend: StorageBackend, conn: MemoryConnection):
        self._backend = backend
        self._conn = conn
        self._indexes: dict[str, IndexSchema] = {}

    def register_index(self, name: str, schema: IndexSchema) -> None:
        """Schema SIN clave 'storage'. La conexión vive en self._conn."""
        self._indexes[name] = schema
```

### D4 — `register_index` config SIN storage key

```python
# libs/cognitive_graph/cognitive_registry.py

def ckg_index_config() -> IndexSchema:
    return IndexSchema(
        fields={"type": "type", "scope": "scope", "label": "statement", ...},
        types={"decision": {}, "refactor": {}, "convention": {}},
        search={"type": "fulltext", "fields": ["statement", "rationale", "tags"]},
        # SIN "storage": {...}  ← la conexión es MemoryConnection (config)
    )
```

### D5 — Factory de forma connection (no path)

```python
# features/main/composition/registry.py

def _build_graph(conn: MemoryConnection) -> CognitiveGraph:
    backend = StorageBackendRegistry.resolve(conn.backend, conn)
    graph = CognitiveGraph(backend, conn)
    register_cognitive_indexes(graph)
    return graph
```

`graph_factory: Callable[[MemoryConnection], Any]` — reemplaza `Callable[[str], Any]`.

### D6 — CPG unifica a un solo backend

`CognitiveCpgRepository` deja de importar `CpgFileRepository` concretamente. Recibe `graph_factory` (como Delta/CKG/Trace) y usa `cg.load/save/search` sobre el índice `cpg`. `CpgFileRepository` se vuelve un adapter interno de `FileBackend` (no expuesto al repo).

### D7 — Resolución de connection desde config (composition root)

> **Corrección de implementación (F6):** `load_memory_connection` NO lee
> `os.environ` directamente. Delega en el **loader SSOT central**
> (`shared/core/engine/infrastructure/config.py`, env > file > defaults). Esto
> cumple el invariante de configuración del proyecto: toda nueva key de config
> debe delegar al SSOT, no leer `os.environ` directamente.

```python
# shared/core/cognitive_graph/config.py

def load_memory_connection(base_dir: str | None = None) -> MemoryConnection:
    """Construye MemoryConnection desde el config central (SSOT)."""
    from shared.core.engine.infrastructure.config import get

    return MemoryConnection(
        backend=get("memory.backend", "file"),
        uri=get("memory.uri", ""),          # file backend lo resuelve desde raíz
        database=get("memory.db", "walker"),
        collection_prefix=get("memory.prefix", ""),
    )

---

## 3. Alternativas evaluadas

| Alternativa | Pros | Contras | Decisión |
|---|---|---|---|
| **A. Mantener file-path, parchear BD con adapter implícito** | Rápido | No resuelve AP-1/AP-2/AP-3; la memoria sigue viendo path; doble fuente de verdad persiste | Rechazada |
| **B. Reescribir `CognitiveGraph` para BD directo** | Control total | Rompe file-backend actual; riesgo alto; sin puerto reutilizable | Rechazada |
| **C. Puerto `StorageBackend` + `MemoryConnection` (elegida)** | Memoria ignora infra; BD = nuevo adapter + config; file sigue funcionando | Refactor moderado de `CognitiveGraph`/`Index`; cambiar firma de factory | Aceptada |

---

## 4. Failure Scenarios

| # | Fallo | Causa raíz | Mitigación | Detección |
|---|---|---|---|---|
| 1 | **`FileBackend` no encuentra `.json.gz` tras refactor** | `CognitiveGraph` dejó de pasar `chain_dir`; `FileBackend` no resuelve ruta | `FileBackend.__init__` recibe `conn.uri` (`file://./data`) y lo resuelve a path real; test de round-trip guarda/lee un doc | `test_storage_file_backend_bdd.py` — store+get+search contra archivo temporal |
| 2 | **Repos CPG rompen por cambio de firma de factory** | `graph_factory` pasó de `Callable[[str], Any]` a `Callable[[MemoryConnection], Any]` | Migración atómica: actualizar los 4 call sites (`cognitive_delta_repo`, `cognitive_ckg_repo`, `repository.py`, `trace_push.py`) + adapter `FileBackend`; test de cada repo con `MemoryConnection` file | `test_module_registry_di_bdd.py`, `test_composition_root_d1.py` |
| 3 | **`register_index` sigue recibiendo clave `storage` legacy** | Caller pasa dict con `"storage"` (AP-9 doble fuente) | `register_index` ignora `storage` si viene; `cognitive_registry.py` elimina la clave; assert en test que schema no contiene "storage" | `test_cognitive_registry_bdd.py` — schema sin key "storage" |
| 4 | **`MemoryConnection` con creds en código** | Dev hardcodea uri con password | `MemoryConnection` solo tiene `uri` desde env (config); lint rule prohíbe literales `postgresql://user:pass`; secret scan en CI | `test_security_no_hardcoded_secrets.py` |
| 5 | **`DbBackend` no implementa `max_key` igual que file** | Semántica de ID incremental difiere entre backends | `StorageBackend` es `Protocol` con todos los métodos obligatorios; test de contrato verifica `max_key` idéntico en File y Db | `test_storage_backend_contract_bdd.py` (parametrizado file/db) |

---

## 5. Consecuencias

### 5.1 Positivas
- **Memoria ignora infraestructura** — repos llaman `cg.store/search`; nunca ven path/uri/creds
- **Migrar a BD = agregar `DbBackend` + cambiar env var** — repos y `CognitiveGraph` intactos
- **Conexión es config** — `MemoryConnection` desde env/secret manager (modelo empresarial)
- **Schema separable de conexión** — `register_index` solo declara fields/types/search
- **Doble fuente de verdad eliminada** (AP-9)

### 5.2 Negativas
- **Refactor moderado** de `CognitiveGraph`/`Index` para extraer IO a `StorageBackend`
- **Cambio de firma de `graph_factory`** toca 4 call sites (migración atómica obligatoria)
- **CPG requiere unificación** de dual-backend (AP-5/AP-6) antes de ser 100% portátil

### 5.3 Déuda técnica eliminada
- ~~Ruta de storage en schema (AP-1)~~ → `MemoryConnection`
- ~~Root = `chain_dir` path (AP-2)~~ → `CognitiveGraph(backend, conn)`
- ~~Factory path-shaped (AP-3)~~ → `Callable[[MemoryConnection], Any]`
- ~~CPG string-munging (AP-4)~~ → `FileBackend` resuelve uri
- ~~CPG dual-backend (AP-5/AP-6)~~ → un solo `StorageBackend` inyectado
- ~~Sin puerto StorageBackend (AP-7)~~ → `StorageBackend` Protocol
- ~~Identidad = path (AP-8)~~ → identidad = `conn.database` + `collection_prefix`

---

## 6. Checklist de segundo orden

| Pregunta | Respuesta |
|---|---|
| ¿Qué módulos importan este? | `shared/core/cognitive_graph/*`, `libs/cognitive_graph/cognitive_registry.py`, `features/main/composition/registry.py`, los 4 repos (`delta_tracking`, `cognitive_knowledge`, `code_analysis`, `event_tracing`) |
| ¿Qué side effects dispara? | `FileBackend` escribe `.json.gz` (igual que hoy); `DbBackend` futuro escribe a BD |
| ¿Qué eventos de dominio publica? | Ninguno nuevo; storage es infra pura |
| ¿Qué tests dependen de este comportamiento? | `test_composition_root_d1.py`, `test_module_registry_di_bdd.py`, `test_cognitive_registry_bdd.py`, `test_scope_guard.py` (indirecto vía dispatcher) |
| ¿Qué cambia en MCP adapter, CLI, CKG, DeltaGraph, Trace? | MCP/CLI: sin cambios. CKG/Delta/Trace: cambian firma de `graph_factory` (reciben `MemoryConnection`). CPG: unifica backend |

---

## 7. Verificación

| Check | Resultado esperado |
|---|---|
| `register_index("ckg", schema)` sin clave `storage` | Schema válido; `MemoryConnection` aporta la conexión |
| `CognitiveGraph(FileBackend(conn), conn).store("ckg", "d1", {...})` | Doc persistido en `.json.gz` (comportamiento actual preservado) |
| `graph_factory(MemoryConnection(backend="file", uri="file:///tmp/x"))` | Retorna `CognitiveGraph` funcional |
| `CognitiveCpgRepository` sin importar `CpgFileRepository` | Solo usa `cg.load/save/search` |
| `MemoryConnection` desde env sin creds en código | `uri` leída de `WALKER_MEMORY_URI` |
| Tests file-backend pasan contra `.json.gz` temporal | Round-trip store/get/search OK |
| 57+ tests existentes siguen verdes | No regresiones en behavior de storage |

---

## 8. Migration path

| Fase | Archivos | Esfuerzo |
|---|---|---|
| **F1:** `MemoryConnection` + `StorageBackend` port | `shared/core/cognitive_graph/connection.py`, `ports.py` | 1h |
| **F2:** Extraer IO de `Index` a `FileBackend` | `shared/core/cognitive_graph/index.py`, nuevo `file_backend.py` | 3h |
| **F3:** `CognitiveGraph(backend, conn)` + `register_index` sin storage | `shared/core/cognitive_graph/__init__.py`, `cognitive_registry.py` | 2h |
| **F4:** Cambiar firma `graph_factory` a `Callable[[MemoryConnection], Any]` | `registry.py`, `bootstrap.py`, 4 repos + `trace_push.py` | 2h |
| **F5:** Unificar CPG a un solo `StorageBackend` | `cognitive_cpg_repo.py` (eliminar `CpgFileRepository` concreto) | 2h |
| **F6:** `load_memory_connection()` desde config SSOT | `shared/core/cognitive_graph/config.py` (+ `backends.py`, delega a `shared/core/engine/infrastructure/config`) | 1h |
| **Total** | | ~11h |

> **Nota:** Tras F1–F6, agregar `DbBackend` (Postgres/Mongo) es F7 aislada (solo un nuevo adapter + env var). Los repos y `CognitiveGraph` no cambian.

---

## 9. Implementación real (Ejecutado — 2026-08-26)

Todo lo planeado en F1–F6 se implementó y los tests de storage/config
(49/49) pasan. Resumen con ubicaciones reales (corrige las referencias de
D2/D7 que apuntaban a ubicaciones hipotéticas):

### 9.1 F1 — `MemoryConnection` + `StorageBackend` (port)
- `shared/core/cognitive_graph/connection.py`: `MemoryConnection` (frozen
  dataclass, config-only — backend/uri/database/collection_prefix).
- `shared/core/cognitive_graph/ports.py`: `StorageBackend` Protocol. **Se
  expandió de 7 a ~24 métodos** (multi-índice completo: `store`, `get`,
  `search`, `delete`, `store_many`, `delete_many`, `max_key`, `add_edge`,
  `get_related`, `find_node_index`, etc.). Corrección de F3: el puerto debe
  ser completo para que `CognitiveGraph` sea IS-A `StorageBackend`.

### 9.2 F2 — IO extraído de `Index` a `FileBackend`
- `shared/core/cognitive_graph/file_io.py`: `read_blob`/`write_blob`
  extraídos de `Index`.
- `shared/core/cognitive_graph/file_backend.py`: `FileBackend(StorageBackend)`
  envuelve un `Index` por `name` e implementa el Protocol completo (incl.
  `find_node_index`/`add_edge`/`get_related`).
- `shared/core/cognitive_graph/index.py`: `Index._path` deriva el nombre de
  archivo desde `name` (`{name}.json.gz`) salvo `storage_path` explícito
  (solo para refs). Se eliminó `data/index.json.gz` por defecto.

### 9.3 F3 — `CognitiveGraph(backend, conn)` + schema sin storage
- `shared/core/cognitive_graph/__init__.py`: `CognitiveGraph` delega **100%**
  a `self._backend`. `isinstance(cg, StorageBackend) == True` (verificado en
  test). `register_index` ya NO lleva clave `storage` (AP-1/AP-9 eliminados).
- `libs/cognitive_graph/cognitive_registry.py`: eliminado el `"storage"`
  hardcodeado.
- Código muerto eliminado: campo `storage_format`, default
  `data/index.json.gz`, `"storage"` hardcodeado en `CognitiveGraph.__init__`.

### 9.4 F4 — Factory de forma connection
- `features/main/composition/registry.py` y `features/main/bootstrap.py`:
  `_build_graph(conn: MemoryConnection | str)`. Backward-compat: acepta `str`
  (path) y lo resuelve vía `memory_data_dir` internamente. Firma canónica
  `Callable[[MemoryConnection], Any]`. `_build_graph_from_path` wrapper para
  `TraceLogger` (respeta layering engine→features).
- Todos los repos construyen `MemoryConnection` vía `load_memory_connection()`:
  `cognitive_delta_repo.py`, `cognitive_ckg_repo.py`,
  `event_tracing/infrastructure/repository.py` (×2), `trace_push.py`.

### 9.5 F5 — CPG unificado a un solo backend
- `features/code_analysis/infrastructure/cognitive_cpg_repo.py`: unificado a
  un único backend vía `load_memory_connection()` + `resolve_storage_backend()`.
  Eliminado el dual-backend `CpgFileRepository` y el string-munging
  `.replace("cpg.json.gz", "")`. `CpgFileRepository` sigue usado por
  consumidores legítimos (bug_service, review_service, query_service,
  metrics_service, builder.stats(), watcher read_fingerprint).

### 9.6 F6 — Configuración SSOT de la conexión
- `shared/core/cognitive_graph/config.py`: `load_memory_connection(base_dir)`
  **delega al loader central** `shared/core/engine/infrastructure/config`
  (env > file > defaults) — NO lee `os.environ` directamente.
- `shared/core/cognitive_graph/backends.py`: `resolve_storage_backend(conn)`
  con `_REGISTRY = {"file": FileBackend}`.
- Keys de config: `memory.backend`, `memory.uri`, `memory.db`,
  `memory.prefix` (en `_DEFAULTS` + `get_all()`).
- **F6-fix:** `config.py` refactorizado para delegar al SSOT; tests verifican
  precedencia file→env.

### 9.7 Paths globales SSOT (registry + manifest)
- `shared/core/engine/infrastructure/config.py`: agregadas
  `registry.projects_file` (default `data/projects.json`) y
  `workflow.manifest` (dinámico → `get_engine_workflow_path()`) a `_DEFAULTS`
  + `get_all()`; helpers `get_projects_registry_path()` y
  `get_workflow_manifest_path()` (resolución absoluta).

### 9.8 Eliminación de workflow.v3 (cero legacy, cero fallback)
- `shared/core/path_resolver/__init__.py`: `get_workflow_dir()` eliminado
  (era búsqueda legacy por proyecto).
- `shared/core/engine/legacy/utils.py`: `canonical_workflow_path()` eliminado.
- `workflow_dir` eliminado de `_DEFAULTS` y `config/config.json`.
- `features/main/composition/container.py` y `features/main/bootstrap.py`: los
  bloques de búsqueda legacy se reemplazaron por única fuente
  `get_workflow_manifest_path()`.
- `features/project/application/services/project_service.py` y
  `features/project/domain/services.py`: `workflow_dir` /
  `workflow_json_path` apuntan al manifiesto canónico global (config central).
- `features/main/prompts/loader.py`: fuente legacy
  `project_dir/workflow.v3/prompts` eliminada (quedó comentario de
  eliminación).
- `features/main/cli_interface/application/handlers.py`: `handle_task`
  inyecta `task_registry` vía
  `_app.module_registry.get_service("task_registry")` (desbloquea dispatch CLI).

### 9.9 Verificación
- 49/49 tests de ADR-060+061 en verde: `test_scope_guard.py` (12),
  `test_storage_port_bdd.py` (8), `test_file_backend_bdd.py` (17),
  `test_memory_config_bdd.py` (11) + integración.
- `isinstance(CognitiveGraph(backend, conn), StorageBackend) == True`.
- `data/projects.json` reparado (entry `walker` apuntaba a tmp dir de pytest).

### 9.10 Pendiente (fuera de alcance de este ADR)
- **MCP externo requiere restart** para que los fixes tomen efecto (el server
  no tiene hot-reload).
- **.gitignore**: agregar `.cognitive/**` (65 archivos) y `.qlty/` (7).
- **F7** (futuro): `DbBackend` Postgres/Mongo como adapter aislado + env var.
- Fallos pre-existentes NO causados por este trabajo: `test_registration_idempotent`,
  `CompositionCycleError` cpg_service↔builder, `test_cpg_status_bdd` (verificado
  vía `git stash` en master).

