# ADR-WALKER-054: Almacenamiento cognitivo multi-proyecto unificado — chain_dir=data_dir por DI, registro único del índice CPG, eliminación de code_paths

> **Status:** Propuesta
> **Fecha:** 2026-08-24
> **Autor:** Kilo (agente) + PO
> **Complementa:** ADR-WALKER-053 (ProjectContext centralizado — mismo espíritu DI: resolver una vez, inyectar resuelto), ADR-WALKER-045/046 (composition root único), ADR-WALKER-052 (DI diferido), BUG-BACKLOG-001 (FTS del CPG lento)
>
> **Ejecutado (2026-08-24):**
> 1. **Pre-requisito:** limpieza completa del binario externo **qlty** — las métricas pasan a ser CPG-nativas (`metrics_service.compute_cpg_metrics`). Ver §5.
> 2. **D1:** eliminación del concepto `code_paths` (schema, manifest, persistencia, detectores, watcher) — el scope de escaneo es `repo_path`. Ver §6.
> 3. **D2/D3:** shared/core NO registra cpg; registro único vía `register_cognitive_indexes` en ambos composition roots. Ver §7.
> 4. **D4/D5:** `chain_dir = data_dir` por DI + storage bare filenames + índice cpg como único almacén (`data/cpg.json.gz`) + regla G3 endurecida. Ver §8.

---

## 1. Problema

### 1.1 Evidencia del día (2026-08-24)

La sesión de análisis del log qlty + backlog (`BUG-BACKLOG-001`) dejó cuatro hallazgos estructurales, todos en la capa de memoria cognitiva (CognitiveGraph + repositorios de índices):

| # | Hallazgo | Evidencia | Consecuencia |
|---|---|---|---|
| 1 | **Off-by-one de `chain_dir` entre escritor y lector del índice cpg** | Writer (`build_service.py:144` `_sync_core`) abre el grafo con `chain_dir = data_dir` (`.cognitive/projects/<pid>/data`) → índice en `data/data/cpg.json.gz` (*shadow index*, 134K, nadie lo lee). Reader (`handlers.py:1192` `_open_cognitive_graph_core`) abre con `chain_dir = resolve_project_root(".")` que retorna **el directorio del proyecto** (`path_resolver/__init__.py:490`) → lee `data/cpg.json.gz` (862K, formato `CpgFileRepository`). | El token_index persistido por el build es **invisible** para el lector → la migración G3 re-corre en cada proceso → FTS lento (timeouts MCP -32001 observados) |
| 2 | **La migración G3 escribe sobre un archivo que no es del índice** | `Index._load()` (`index.py:217-250`) detecta `schema_hash` vacío ≠ actual, reconstruye el TokenIndex y llama `self._save()` (línea 244) → **sobrescribe `data/cpg.json.gz` en formato CognitiveGraph** (`docs`/`edges`/`schema_hash`) mientras `CpgFileRepository.load()` (`repository.py:52`) espera la clave `nodes`. | Ciclo rebuild↔clobber: búsqueda → clobber del CPG → build lo regenera → búsqueda... (builds repetidos en `qlty-cli.2026-08-24` 12:17/15:17/15:39/17:xx son consistentes) |
| 3 | **Doble registro del índice cpg + dos composition roots divergentes** | `shared/core/cognitive_graph/__init__.py:77-88` auto-registra cpg con `search_handler` legacy (sin fulltext, ignora `text` en `skip_keys`). `libs/cognitive_graph/cognitive_registry.py:60-74` registra cpg con `search.fulltext` y sin handler. Y el runtime tiene DOS fábricas: `features/main/bootstrap.py:250` (`CognitiveGraph` con auto-register) vs `features/main/composition/registry.py:314` (`register_cognitive_indexes`). | Comportamiento distinto según qué composition root arranque el proceso; `memory_schema` del índice cpg no muestra fulltext (confirma el drift) |
| 4 | **`code_paths` en el manifest referencia directorios muertos** | `walker status` → `code_paths: ["features", "libs", "tests", "to_clean_next"]`; `to_clean_next` no existe en disco. El build pasa `paths[:10]` a `qlty check --fix --no-fail` (`build_service.py:388`) → 3× ERROR "path does not exist" en el log qlty. | El scope de escaneo duplica a `repo_path` y puede apuntar a rutas inexistentes → abortos de qlty y watcher con scope inconsistente |

### 1.2 Por qué importa ahora

La app es **multi-proyecto**: cada proyecto tiene su raíz cognitiva canónica `.cognitive/projects/<pid>/data/` (resuelta por `get_data_dir(project_id)`, `path_resolver/__init__.py:307`). Hoy cada callsite resuelve esa raíz de forma distinta (`resolve_project_root` → dir del proyecto; `data_dir` directo; `cpg_path.replace("cpg.json.gz","")`), y los archivos de memoria terminan en lugares inconsistentes (`data/data/cpg.json.gz` vs `data/cpg.json.gz`). La única fuente de verdad debe ser el `data_dir` del proyecto activo, resuelto UNA vez en main e inyectado por DI — exactamente el espíritu de ADR-WALKER-053 aplicado al almacenamiento.

---

## 2. Decisión

**Unificar el almacenamiento cognitivo: `chain_dir = data_dir` inyectado por DI desde main como única fuente de verdad; un solo registro del índice CPG en el composition root; eliminar `code_paths` del manifest.**

### D1 — Eliminar `code_paths` del manifest de proyecto

- El campo `location.code_paths` se elimina de `project.json` (schema `features/project/domain/schema.py:44`, manifest `values.py:149-211`) y de su persistencia.
- El scope de escaneo del CPG, del watcher y de las métricas pasa a ser **`repo_path` completo** (`resolve_code_paths()` en `build_service.py:343-353` cae al fallback `[code_root]`; `code_index_orchestrator_service.py:214-215` ya hace `[project_root]` si vacío; `FileWatcher` observa `project_root` si `code_paths` vacío).
- `get_code_paths` (`path_resolver/__init__.py:417`) y los detectores de code_paths quedan deprecados (remoción en un segundo paso, una vez verificado que ningún manifest los consume).
- **Motivo**: `code_paths` es una lista manual de subdirectorios que duplica a `repo_path`, se desincroniza (referenció `to_clean_next` eliminado) y rompió `qlty check --fix`. El repo completo es el scope coherente.

### D2 — `shared/core/cognitive_graph` NO registra el índice cpg

- `register_defaults()` (`shared/core/cognitive_graph/__init__.py:40-52`) registra SOLO `ckg`, `delta`, `trace` y los índices de referencia (`cpg.ref-*`). Se eliminan `_register_cpg_index()` (líneas 77-88) y `_make_cpg_search_handler()` (90+).
- `CognitiveGraph` (shared/core) queda como infraestructura genérica sin conocimiento del dominio CPG (refuerza ADR-045: los schemas viven en el composition root).

### D3 — Registro único del índice cpg: `register_cognitive_indexes` desde el composition root

- El ÚNICO lugar que registra `cpg` es `register_cognitive_indexes()` (`libs/cognitive_graph/cognitive_registry.py:105-114`, con `cpg_index_config()` fulltext sin handler) invocado por `features/main/composition/registry.py:314-315` (`_build_graph`) y `:325-326` (`_build_searcher`).
- Se alinea `features/main/bootstrap.py:242-266` (`_build_graph`/`_build_searcher`) al mismo patrón (usar `register_cognitive_indexes` en vez de auto-register), eliminando la divergencia de comportamiento entre los dos composition roots.

### D4 — `chain_dir = data_dir` por DI desde main (única fuente de verdad)

- `main` resuelve el `data_dir` del proyecto activo (`get_data_dir(project_id)`) y lo inyecta como `chain_dir` a la fábrica `cognitive_graph`/`cognitive_searcher` (DI estilo ADR-053).
- Los `storage.path` de los índices en `libs/cognitive_graph/cognitive_registry.py` pasan de `"data/<file>.json.gz"` a filenames directos `"<file>.json.gz"` (relativos a `chain_dir`): `ckg.json.gz`, `cpg.json.gz`, `delta.json.gz`, `trace.json.gz`.
- **Resultado**: todos los archivos de memoria viven en `.cognitive/projects/<pid>/data/` — un solo directorio canónico, idéntico para escritores y lectores. Desaparece `data/data/cpg.json.gz`.
- Todos los callsites de `get_module_service("cognitive_graph")(X)` deben pasar el `data_dir` canónico (hoy ya lo pasan `_sync_core`/`_sync_core_delta`/`_sync_to_cognitive` en `build_service.py:144,167,594`; se corrigen `handlers.py:1192`, `trace_log.py:64,173`, `event_tracing/repository.py:212`, `cognitive_ckg_repo.py`, `cognitive_delta_repo.py`, `trace_push.py`, `sprint_tracking_service.py:32,55`).

### D5 — El índice cpg ES el almacén en `data/cpg.json.gz` (persistencia en build-time)

- `data/cpg.json.gz` es el ÚNICO archivo cpg (se elimina el shadow `data/data/cpg.json.gz`).
- **CognitiveGraph es el único writer**: el build (`_sync_core`) guarda los nodos CPG **completos** como docs del índice (no el subconjunto `name/type/file/line` actual) + edges + `token_index` en build-time. Esto es la **Opción 2 de BUG-BACKLOG-001**: FTS rápido desde el primer proceso, sin migración en lectura.
- `CpgFileRepository.load()/save()` (`repository.py:52-88`) se adaptan como capa de compatibilidad que lee/escribe el mismo formato a través del índice (los consumidores de `nodes`/`edges` no cambian).
- **Regla G3 endurecida**: la migración de schema en `Index._load()` solo persiste (`_save()`) si el archivo ya es formato CognitiveGraph (contiene `schema_hash`); si el archivo es ajeno (sin `schema_hash`), se reconstruye en memoria **sin escribir** — nunca clobber de un archivo que el índice no posee.
- Migración one-shot de los archivos existentes (`cpg.json.gz` formato CPG → formato índice) al primer build posterior.

### 2.x Allowlist de excepciones

(vacía al aprobar; toda excepción cita issue y fecha de caducidad)

---

## 3. failure_scenarios

| # | Fallo | Causa raíz | Mitigación | Detección |
|---|---|---|---|---|
| 1 | El índice cpg se reconstruye (y el FTS tarda minutos) en cada proceso nuevo | Writer y reader usan `chain_dir` distintos → el token_index persistido es invisible para el lector | D4 unifica `chain_dir=data_dir` por DI; D5 persiste el token_index en build-time | Test de contrato: tras un build, un proceso nuevo hace `search("cpg", {"text": X})` sin rebuild (assert en log de "reconstruyendo") |
| 2 | Una búsqueda textual sobrescribe `data/cpg.json.gz` y "rompe" el CPG para `CpgFileRepository.load()` (clave `nodes` perdida) | Migración G3 hace `_save()` sobre un archivo que el índice no posee (formato CPG sin `schema_hash`) | Regla G3 endurecida: solo persiste si el archivo ya es formato índice; D5 convierte al índice en el único writer | Test: `search` con texto NO modifica `mtime` ni formato de un `cpg.json.gz` ajeno |
| 3 | `code_paths` vuelve a referenciar un directorio inexistente y `qlty check --fix` aborta el build | Lista manual de subdirectorios desincronizada con el repo | D1 elimina el campo del manifest; el scope cae a `repo_path` (fallback ya existente) | Grep-CI: ningún `project.json` contiene `code_paths`; log qlty sin "path does not exist" |
| 4 | Proceso arrancado por el composition root viejo (`bootstrap.py`) registra cpg con el handler legacy y las búsquedas textuales devuelven 0 hits o todo | Dos fábricas divergentes de `cognitive_graph` | D3 alinea ambos composition roots a `register_cognitive_indexes`; test de equivalencia de schemas entre roots | Test: `memory_schema` de cpg muestra `search.fulltext` en AMBOS roots |
| 5 | La migración de `data/data/cpg.json.gz` → `data/cpg.json.gz` pierde nodos o edges durante el primer build post-deploy | Formato distinto entre shadow index y CPG principal | D5: el build regenera el índice completo desde el CPG (no hay merge); verificación de counts (5878 nodos / 6169 edges) post-build | Test de integridad: `status` reporta mismo `total_nodes`/`total_edges` antes y después |
| 6 | Cached `data_dir` de un proyecto cambia (multi-proyecto con server CWD distinto) y las memorias se escriben en el proyecto equivocado | `chain_dir` resuelto desde CWD en vez del `project_id` activo | D4: `main` resuelve `data_dir` por `project_id` (precedencia explícito > ambiental, ADR-053) y lo inyecta; las tasks no resuelven rutas | Test multi-proyecto: dos `project_id` distintos en el mismo proceso escriben en dos `data/` distintos |

---

## 4. Consecuencias

**Positivas**
- **FTS del CPG rápido de raíz** (cierra BUG-BACKLOG-001 con la Opción 2): el token_index viaja con el build, no con la primera búsqueda de cada proceso.
- Un solo directorio de memoria por proyecto (`.cognitive/projects/<pid>/data/`) — desaparece el shadow `data/data/` y la clase entera de bugs "lector no ve lo que escribió el writer".
- Un solo registro del índice cpg (composition root) — se elimina el drift de configuración entre shared/core y libs, y la divergencia entre composition roots.
- Sin `code_paths`: el scope es `repo_path`, coherente entre build/watcher/metrics/qlty; los abortos por directorios muertos mueren.
- Refuerza la arquitectura multi-proyecto real (ADR-053): la raíz cognitiva se resuelve una vez en main, no en cada servicio.

**Negativas / costos aceptados**
- `CpgFileRepository` requiere un adaptador de formato (lectura del índice) — capa de compatibilidad acotada que mantener.
- Migración one-shot de los archivos de memoria existentes (formato CPG → formato índice para cpg) — coordinada con el primer build.
- Cambio de `storage.path` en los 4 índices — cualquier consumidor que construya rutas a mano (grep `data/ckg.json.gz` etc.) debe migrar al resolver canónico.
- Eliminar `code_paths` toca schema, manifest, detectores y ~6 tests que lo afirman — refactor transversal acotado.

**Relación**: ejecuta el espíritu DI de ADR-WALKER-052 §D3 y ADR-WALKER-053 (resolver una vez, inyectar) aplicado al almacenamiento; corrige el supuesto roto que dejó 8e64347 (fulltext del CPG) sin unificar la raíz del índice; cierra BUG-BACKLOG-001.

---

## 5. Ejecutado — limpieza del binario externo qlty (pre-requisito)

Eliminado TODO uso de la librería tercerizada **qlty** (CLI de métricas/smells/lint). Las métricas pasan a ser **CPG-nativas** (`metrics_service.compute_cpg_metrics`), derivadas del mismo build del grafo.

**Módulos eliminados:**
- `features/code_analysis/infrastructure/codereview/qlty.py` (wrappers run_metrics/smells/check/fix)
- `features/code_analysis/infrastructure/codereview/cache.py` (MetricsCache qlty)
- `features/code_analysis/infrastructure/metrics.py` (CLI qlty + parseo de tablas/SARIF)
- `features/code_analysis/infrastructure/metrics_cache.py` (IncrementalMetricsCache)
- `features/cognitive_knowledge/infrastructure/qlty.py` + `QltyScanner`
- `.qlty/` (config del CLI) — `git rm`

**Reescritos (CPG-native / ast-grep-only):**
- `review_service.py` → `compute_cpg_metrics` + dead code del CPG + deep ast-grep; se conserva el contrato `ReviewResult` y la firma (parámetro `cache` legacy).
- `build_service.py` → sin `_auto_fix` (qlty check --fix) ni `_ensure_qlty_config_core`; `_persist_quality_report` usa métricas CPG-nativas.
- `codereview/{config,analysis,deep,main,__init__}.py` → sin QLTY/QLTY_AVAILABLE; deep patterns incondicionales (`deep_analyze_file`).
- `dependency_checker.py` → solo ast-grep; `Makefile`/`bin/install-deps.sh`/`.gitignore` sin qlty.

**Tests:** escenarios qlty eliminados de `dependency_checker.feature` y `cpg_infra_caches.feature`; suite de los archivos tocados en verde. Fallos preexistentes (DAG gate / orden de tests) no relacionados, confirmados idénticos con y sin estos cambios.

---

## 6. Ejecutado — D1: eliminación de `code_paths` (scope = repo_path)

**Eliminado del schema y la persistencia:**
- `features/project/domain/schema.py` → campo `location.code_paths` fuera de `PROJECT_SCHEMA`.
- `features/project/domain/values.py` → `code_paths` fuera de `ProjectPaths` y `ProjectManifest`.
- `features/project/domain/entities.py` / `infrastructure/repositories.py` → fuera de la proyección/persistencia.
- `features/project/application/services/project_service.py` → lecturas de `location.code_paths` eliminadas.
- `shared/core/path_resolver/__init__.py` → `get_code_paths()` ELIMINADA; `resolve_source_code_dirs()` = repo completo.

**Detectores/validación:**
- `use_cases.py` / `project_detector.py` → `detect_code_paths` deprecada (retorna `[]`); sin `code_paths` en `detect_all`.
- `detect_project_context.py` → sin `code_paths` en el output.
- `health_report_analyzer.py` + `HealthReportAnalyzer` → sin "missing code_paths".
- `core/engine/workflow/nodes/project_init.node.json` → `code_paths` fuera del output_schema de DETECT.

**Scope = repo_path (watcher/build/orchestrators):**
- `FileWatcher` → sin param `code_paths`; `watch_dirs = [project_root]`. Llamadores actualizados (handlers, project_use_cases, check_health, prepare_project, build_service).
- `BuildGraphUseCase` → sin param `code_paths`; `resolve_code_paths()` = `[repo_path|code_root]`. `CPGBuilder`/`CpgService` actualizados.
- `code_index_orchestrator_service._build_paths()` → `[project_root]`.
- `review_service._resolve_default_paths()` y `metrics_service._count_loc()` → sin paths de manifest.
- `_write_project_json` (prepare + project_use_cases) → `location` sin `code_paths`.
- Prompts de cierre de sprint → "across the full repo".

**Tests:** fixtures sin `code_paths` (`path_resolver_project_root`, `cpg_project_integration`, `cpg_use_cases`, `cpg_status_bdd`, `build_update_incremental`); feature `cpg_project_integration` sin el step de code_paths. Contrato de gates CPG-native: `dead_code` + `loc_per_file` (cyclo omitido — ver §5). Sin regresiones vs baseline.

**Pendiente (data-cleanup):** ✅ **RESUELTO** — `location.code_paths` del manifest walker fue **eliminado por completo** (no quedó ni vacío): `grep code_paths data/ .cognitive/` → 0 resultados. También se limpió el ruido que el wrapper MCP de `update_project` inyectaba en `identity` (`_payload`, `_channel`, `_project_ctx`, `task`, etc.). Se cumple la mitigación del failure_scenario #3: ningún `project.json` contiene `code_paths`.

---

## 7. Ejecutado — D2/D3: registro único del índice cpg (composition root)

**D2 — shared/core NO registra cpg:**
- `shared/core/cognitive_graph/__init__.py` → `register_defaults()` registra SOLO `ckg`, `delta`, `trace` + ref indexes (`cpg.ref-*`). Eliminados `_register_cpg_index()` y `_make_cpg_search_handler()` (handler legacy que leía `CpgFileRepository` e ignoraba `text`).
- `CognitiveGraph` (shared/core) queda como infraestructura genérica sin conocimiento del dominio CPG (ADR-045).

**D3 — Registro único vía `register_cognitive_indexes`:**
- `features/main/bootstrap.py` `_build_graph`/`_build_searcher` → ahora llaman `register_cognitive_indexes(graph)` (antes usaban auto-register → divergencia). Ambos composition roots (bootstrap + `composition/registry.py`) producen el MISMO esquema.
- `shared/core/memories/query_service.py` (`MemoryQueryService`, ruta kg_search/status) → `register_cognitive_indexes(self._cg)` al construir.

**Tests:** `test_cpg_result_pattern` / `test_cpg_build_result` (construyen `CognitiveGraph` directo y usan el índice cpg) → agregan `register_cognitive_indexes`. Verificado con test de equivalencia: cpg con `has_fulltext()` y `_search_handler is None` en ambos roots; `MemoryQueryService` indexa cpg; defaults de shared/core NO contienen cpg. Sin regresiones vs baseline (CompositionCycleError del DAG gate preexistente, idéntico con/sin cambios).

---

## 8. Ejecutado — D4/D5: chain_dir=data_dir por DI + índice cpg como único almacén

**D4 — storage paths a filenames directos + chain_dir = data_dir:**
- `libs/cognitive_graph/cognitive_registry.py` → storage `data/<file>.json.gz` → `<file>.json.gz` (`ckg.json.gz`, `cpg.json.gz`, `delta.json.gz`, `trace.json.gz`).
- Nuevo helper `memory_data_dir(chain_dir)` en el registry (resolución canónica vía `ProjectService.resolve_paths` → `.cognitive/projects/<pid>/data`; fallback defensivo). Ambos composition roots (`bootstrap.py` y `composition/registry.py`) lo aplican en `_build_graph`/`_build_searcher` → **chain_dir = data_dir inyectado por DI** (única fuente de verdad multi-proyecto).
- Resultado: todos los archivos de memoria viven en `.cognitive/projects/<pid>/data/`. Desaparece el shadow `data/data/cpg.json.gz`.

**D5 — El índice cpg ES el almacén (`data/cpg.json.gz`):**
- `Index` (shared/core) → passthrough de `metadata` (claves top-level del dominio) en `_save`/`_load` + API `set_metadata`/`sync_edges`/`get_docs`/`get_all_edges`/`get_metadata` (facade `CognitiveGraph` idem). El build guarda los nodos **completos** como docs + edges + metadata (versión, fingerprint, business_layer, quality_report, bug_proposals, etc.).
- `CpgFileRepository` → capa de compatibilidad sobre el índice: `load()`/`save()` leen/escriben el formato del índice (docs→nodes, edges, metadata→top-level). `load_graph()`/`save_graph()` intactos. Migración natural: un `cpg.json.gz` legacy (formato CPG) carga en el índice vía `_load()`.
- **Regla G3 endurecida**: la migración de schema en `Index._load()` solo persiste si el archivo ya contiene `schema_hash` (archivo del índice); si es ajeno, se reconstruye en memoria SIN escribir — nunca clobber.
- Contrato de robustez preservado: nodos inválidos en `_sync_core` → Failure explícito (no skip silencioso).

**Verificación:** build funcional en tmp → índice único en `data/cpg.json.gz` (sin `data/data/`), docs completos, FTS nativo OK; `CpgFileRepository.load()` lee nodes/edges/metadata de vuelta. Tests `test_cpg_build_result` + `test_cpg_result_pattern` (6) y `test_cpg_infra_caches`/`test_trace_event_publisher` en verde; ruta MCP (recalling_knowledge) operativa. Sin regresiones vs baseline (failures preexistentes idénticos).

**Pendiente:** el `data/cpg.json.gz` del proyecto walker es legacy (formato CPG) → el primer `build_code_index` posterior lo migra al formato índice y persiste el token_index (FTS rápido desde el primer proceso).