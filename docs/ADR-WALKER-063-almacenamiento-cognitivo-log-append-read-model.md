# ADR-WALKER-063 — CognitiveGraph (infraestructura): del snapshot O(N) al storage transaccional (SQLite)

- **Status:** Propuesta aprobada — pendiente de implementación
- **Date:** 2026-08-26 (refactorizado 2026-08-27: compresión, tabla única, criterios de aceptación, versionado; **decisión de backend: SQLite en Fase 1**; contrato real del puerto + impacto medido en capas/entidades §6.5-§6.6; invariantes IV-6..IV-10, errores custom y limpiezas por fase §6.1)
- **Autor:** Walker agents (análisis de tercer orden, con el usuario)
- **Complementa:** ADR-WALKER-054 (almacenamiento cognitivo unificado chain-dir/data-dir), ADR-WALKER-055 (deuda técnica estructural — F2 write amplification), ADR-WALKER-061 (storage como puerto: file-path a connection-config), ADR-WALKER-062 (mantenibilidad monolith puertos DI CI)
- **Hipótesis:** El problema es de **infraestructura** (CognitiveGraph), no de ninguna aplicación. CognitiveGraph tiene su PROPIO dominio — el **dominio de los datos** (no el de negocio): sus entidades, reglas e invariantes, y sus casos de uso (store/append/search/compact). El fix se hace **sin cambiar el propósito de CognitiveGraph** (grafo + búsqueda ES-like + transparencia al dominio): el puerto `StorageDb` (§3.4) permanece y cambia el backend — del blob `.json.gz` a **SQLite (stdlib `sqlite3`, transacciones WAL)** como write-side durable, con el **read model en memoria** reconstruido desde la BD (búsqueda idéntica a hoy). El puerto `StorageBackend` (ADR-061), elevado a `StorageDb` (§3.4), permite hacerlo sin tocar el dominio de negocio.

---

## 1. Problema (síntesis)

**CognitiveGraph es la infraestructura de almacenamiento y recuperación de TODAS las memorias** (CPG/CKG/Delta/Trace): una "base de datos" en ficheros que almacena como **grafo** (`nx.DiGraph`) y recupera como **Elasticsearch** (type/field/token index). El tracer y el resto son **aplicaciones** de esa infraestructura.

La deficiencia es de infraestructura: la persistencia usa un **blob único `.json.gz` por índice** — toda escritura (incluso 1 doc) reescribe el índice completo (O(N)). El trace es la aplicación más expuesta (append-only; escribía por evento: 27 reescrituras por llamada MCP ≈ 35s de overhead a N=57k) y se mitigó con batching a nivel aplicación. Pero el problema de fondo es del motor: **la infraestructura no tiene write-side incremental** (snapshot read-modify-write para todos los ciclos de vida). El backlog de Delta (`backlog.jsonl`) demuestra el patrón correcto (append JSONL) — pero fuera del motor y sin búsqueda.

> **Alcance del cambio:** los índices blob (`CPG`, `CKG`, `Delta-specs`, `Trace`). El `Delta-backlog` (`backlog.jsonl`) **ya es correcto** (append JSONL O(1)) y queda fuera del alcance: se conserva en su formato.

### 1.1 Problemas identificados (tabla única)

> Evidencia a fecha 2026-08-26; las líneas se re-verifican al implementar.

| # | Problema | Evidencia | Severidad |
|---|---|---|---|
| P1 | **Write amplification**: cada write reescribe el índice completo (O(N)) | `index.py:_save` (300-332), `_store_core` (402-441) | Alta |
| P2 | **Infra sin write-side incremental**: snapshot read-modify-write para todos los ciclos de vida | `index.py:_save`; todos los repos usan `store*` | Alta |
| P3 | **Conflación log ↔ índice**: el read model (search) y el write model (persistencia) en la misma estructura — no hay "registrar" vs "proyectar" (CQRS-lite sin completar) | `Index` único para write+search; `has_kg_query` lee snapshot | Alta |
| P4 | **Load-all-to-add-one**: `_load()` lee todo el índice para añadir 1 doc | `index.py:_load` (166-296) | Media |
| P5 | **`_load()` redundante por write** | `store()` 407 + `_store_core` 421 | Baja |
| P6 | **Durabilidad sacrificada por batching** (parche de aplicación, reversible) | `trace_log.py:_pending/_flush_pending` | Media |
| P7 | **God-object `Index`**: storage + search + grafo + locks + migración en 1 clase — cambiar persistencia toca search | `index.py` (57-96) | Media |
| P8 | **Formato gz sin append/random-access** (stream compression bloquea escrituras incrementales) | `file_backend.py`, `write_blob` | Media |

P1 es el costo raíz; P2–P4 son su mecanismo; P6 es una mitigación de aplicación que el diseño elimina; P7/P8 condicionan la implementación.

---

## 2. Análisis (síntesis)

**Estructura actual — write y read model acoplados (CQRS-lite sin completar):**

```
Dominio (repos: trace_log, cognitive_ckg_repo, cpg_repo)
  → puerto StorageBackend (ports.py)
    → FileBackend (file_backend.py)
      → Index (nx.DiGraph + _docs + type_index/field_index + token_index)
        → .json.gz (blob único)
```

El modelo mental embebido: "cada memoria es UN documento que se carga entero, se muta y se guarda entero" (arquetipo *file-as-database*). Con N creciente, cualquier write degrada. Los fixes en espiral (batching, `store_many`, `replace_blob`) **amortizan el O(N) pero nunca lo eliminan** — y cada vuelta añade complejidad accidental (estado, umbrales, atexit) sobre un núcleo que no escala.

**Arquetipos en juego:** file-as-database (snapshot — hoy para todos), event log (append-only — trace y backlog por naturaleza), read model (proyección para search). **El trace es un event log puro: nace, se appendea, se consulta como evidencia, nunca se actualiza ni borra.** Dos familias de solución probada: (1) **segmentos tipo LSM/Lucene** (append O(1) + compactación) y (2) **BD transaccional embedded** (SQLite: B-tree + WAL, escrituras O(log N) sin reescribir el store). Ambas eliminan la write amplification; SQLite la resuelve **sin ingeniería propia de formato** (transacciones, delete real, crash recovery nativo). Esa es la vía elegida.

**Ciclos de vida del dominio (el mismatch es el problema de diseño raíz):**

| Memoria | Ciclo de vida del dominio | Storage que le corresponde |
|---|---|---|
| **CPG** | build (reemplazo total) → update incremental → lectura pesada | transaccional: `replace_blob` atómico (DELETE+INSERT) + increments |
| **CKG** | acumular facts → batch → search | transaccional: `store_many` upsert |
| **Delta-specs** | por sprint; specs se crean/actualizan (mutable) | transaccional: upsert por `_id` |
| **Delta-backlog** | append-only, lectura por prioridad | **log JSONL append** (fuera de alcance; ¡ya correcto!) |
| **Trace** | **event log puro**: nace, se appendea, se consulta como evidencia, nunca se actualiza ni borra | tabla `events` append-only (orden por `seq`) + read model derivado (hoy: snapshot → mismatch) |

**Conclusión del marco:** no hay que rediseñar el marco (Clean/Hexagonal ADR-062, domain-first 0-5, puertos ADR-061, composition root ADR-045) — está bien diseñado, contiene el escape hatch correcto (`StorageBackend`) y ya tiene el arquetipo probado (`backlog.jsonl`). Hay que **elegir el storage correcto dentro del puerto** para cada ciclo de vida.

---

## 3. Dirección de diseño

### 3.1 Objetivo: infraestructura CognitiveGraph — storage transaccional + read model en memoria

El fix se escopa en la **infraestructura** (detrás del puerto `StorageBackend`, en la capa de ficheros), no en una aplicación:

1. **Write-side:** cada write es un **INSERT transaccional en SQLite** (B-tree, O(log N)) — sin reescribir el store completo. `COMMIT` = punto de durabilidad (síncrono, sin batching de aplicación).
2. **Read-side:** la búsqueda opera sobre el **read model en memoria** reconstruido desde la BD (misma API: `store`/`search`/gates).
3. **Sin merge/compaction por escritura:** no hay segmentos que fusionar; la retención (ADR-064 S2) + `VACUUM` opcional acotan el tamaño.
4. **Beneficiarios:** TODOS los índices (trace, CPG, CKG, Delta). El batching del `TraceLogger` (mitigación de aplicación) se vuelve **innecesario y reversible**.

### 3.2 Alcance y alternativas

- `SqliteBackend` (`sqlite3` de la stdlib, WAL) cubre el 80% del costo actual para todos los índices; el read model en memoria queda intacto (búsqueda idéntica, cero riesgo en gates).
- `Delta-backlog` (`backlog.jsonl`): fuera de alcance — ya es correcto (append JSONL).
- Si se necesita un **log plano auditable** (trace como evidencia en ficheros crudos cateables), `SegmentFileBackend` (JSONL) es la alternativa — no el default: exige ingeniería de DB propia (tombstones, compact atómico, recencia multi-fichero — bugs A1–A5 de la crítica).
- Evolución futura (no bloquea Fase 1): búsqueda vía SQL/FTS5 (§3.7) y/o servidor.

### 3.3 Criterios de decisión

- **SQLite (transacciones WAL) es el write-side durable**; el índice en memoria es el **read model reconstruible** — nunca fuente de verdad.
- El puerto se eleva a `StorageDb` (§3.4): **conserva los 24 métodos actuales de `StorageBackend` (ports.py)** y añade `append`/`compact`/`flush` (en `SqliteBackend`: `append`=INSERT, `compact`≈no-op, `flush`≈COMMIT); **los dominios no se tocan** (IV-4). El `FileBackend` evoluciona a `SqliteBackend` (implementa los 27 métodos — contrato completo en §7.1).
- Migración gradual: doble escritura (blobs `.json.gz` legacy + sqlite) → import transaccional → gates E2E → cutover (§7.7, FS-6).
- La búsqueda (ES-like) NO cambia en Fase 1: `_type_index`/`_field_index`/`_token_index` se construyen en memoria desde la BD. SQL/FTS5 es evolución (§3.7), no requisito.

### 3.4 La interfaz `StorageDb` (puerto DB-like) + capa de BD

`StorageDb` es el puerto que hace que CognitiveGraph se comporte como una **base de datos**, con **transparencia total hacia los dominios** y con **invariantes que impiden que los problemas de hoy se repitan**. Es la evolución del `StorageBackend` (ADR-061): el contrato sube de "guardar/leer blobs" a "comportarse como una base de datos (grafo + búsqueda ES-like + escritura eficiente)".

**Contrato (DB-like) — `StorageDb` = `StorageBackend` real (ports.py, 24 métodos, sin cambios) + `append`/`compact`/`flush`:**
```
# Los 24 métodos actuales de StorageBackend se conservan íntegros (sin cambios):
#   registro:      register_index
#   CRUD docs:     store, store_many, get, delete, delete_many, max_key, search
#   grafo/cross:   add_edge, remove_edge, get_edges, get_related, find_node_index
#   blob/meta:     replace_blob, sync_edges, set_metadata, get_metadata,
#                  get_docs, get_all_edges, keys, upsert
#   introspección: schema_info, metrics, list_indexes
# + NUEVOS (27 en total, contrato completo en §7.1):
append(index, docs)        # write-side append-only (trace; en Sqlite: INSERT en lote)
compact(index)             # en Sqlite: no-op (retención + VACUUM)
flush(index)               # en Sqlite: COMMIT explícito
# IMPERATIVO: nada de lo que consume hoy la facade (get_related, max_key,
# add_edge, keys, get_docs...) puede desaparecer del contrato — repo de CKG usa
# max_key (decision-NNN), repos Delta/CPG usan add_edge/replace_blob, tests
# usan delete_many/get_all_edges (H5/H6, §6.5).
```
Los dominios consumen la facade (subconjunto); el contrato COMPLETO (27 métodos) es el del apéndice §7.1.

**Invariantes del puerto (los problemas de hoy NO se repiten):**
- **IV-1 (no write amplification):** ningún write reescribe el índice completo. Los writes son **transacciones SQLite** (INSERT/upsert, O(log N) sobre B-tree); no existe reescritura del store por evento.
- **IV-2 (durabilidad sin batching):** cada write commitea **síncrono** con WAL (`synchronous=NORMAL`) — crash-safe, sin buffers de aplicación que sacrifiquen durabilidad (elimina P6; el batching del `TraceLogger` deja de existir).
- **IV-3 (search sobre read model):** `_type_index`/`_field_index`/`_token_index` se construyen en memoria desde la BD; la búsqueda nunca escanea el store completo.
- **IV-4 (transparencia al dominio):** los dominios (`TraceLogger`, `cognitive_*_repo`) consumen SOLO el puerto; no conocen SQL/tablas — sin cambio de contrato.
- **IV-5 (tamaño acotado):** la retención (ADR-064 S2) acota el tamaño de la BD; `VACUUM` opcional la compacta. Sin ficheros de segmentos que acumular.
- **IV-6 (no fallos silenciosos):** toda operación que falla **falla ruidosamente** — `Result.failure(E)` con error custom de su fase (§6.1) o excepción custom propagada; jamás `None`/`{}` que parezcan éxito, jamás `except: pass`.
- **IV-7 (no retornos vacíos ambiguos):** `get`/`search`/`metrics` nunca devuelven "nada" de forma ambigua: un sin-hits es un shape completo explícito (`{"hits": [], "total": 0}`) o el error custom de la fase si el contrato exige datos; el invocante decide, el backend nunca "no dice nada".
- **IV-8 (funcional determinista):** implementación con `returns` (`Result[T, E]`), funciones puras/casi-puras; mismo estado committed + mismos inputs → **mismo resultado**; sin estado mutable global (el read model se refresca por la regla explícita §6.3a).
- **IV-9 (sin fallbacks ni mantenimiento legacy):** prohibido "si sqlite falla, caer a `.json.gz`" y prohibidos modos compat activables en runtime. La única doble escritura es la migración (§7.7), temporal y con retirada obligatoria; tras el cutover NO existe camino v1 (guarda ruidosa §6.4).
- **IV-10 (código limpio):** tras cada fase: cero dependencias nuevas no aprobadas (Fase 1: solo stdlib `sqlite3`), cero archivos muertos (barrido de imports), cero ramas no ejecutadas (cobertura de errores), registries con test de completitud (AGENTS.md).

**Implementación de capa de BD (`SqliteBackend`, evolución de `FileBackend`):**
- Un fichero por proyecto: `<data_dir>/cognitive.sqlite3` (WAL) con tablas `docs`/`nodes`/`edges`/`events`/`meta` (§7.3).
- `append`: INSERT transaccional. `_load`: SELECT y reconstruir `Index` en memoria. `delete`: DELETE real transaccional. `replace_blob`: DELETE+INSERT atómico. `compact`: no-op (retención + VACUUM). `search`: sobre el read model en memoria (igual que hoy). `max_key` (secuencias CKG `decision-NNN`): `SELECT MAX(id)` con prefijo o contador en `meta` (H5, §6.5). Edges cross-index (`add_edge`/`get_related`/`find_node_index`): tabla `edges` con columna owner y `src`/`dst` textuales, sin FKs (H6, §6.5).
- El puerto se adelgaza (quitar `compact`/`flush` no-op) en una revisión futura — no bloquea Fase 1.

**Transparencia y evolución:**
- La facade `CognitiveGraph` mantiene la misma API; `StorageBackend` (ADR-061) se eleva a `StorageDb`.
- Un futuro `StorageDbServerBackend` implementa el MISMO puerto sin tocar dominios (§3.7).

### 3.5 El dominio de CognitiveGraph es el dominio de los DATOS (marco breve)

CognitiveGraph NO es el dominio del negocio — CPG/CKG/Delta/Trace son los dominios de negocio que lo **usan**. Su dominio es el **dominio de los datos** (infraestructura): entidades (`Index`, `Node`/`Edge`, `Document`, `Schema`, `Event` — no hay "Segment": eso es infraestructura, no dominio), reglas (grafos consistentes, índices secundarios siempre alineados, durabilidad transaccional, searchabilidad O(1), inmutabilidad de eventos trace) y casos de uso (`store`/`append`/`replace_blob`/`get`/`search`/`compact`/`delete`/`flush`). Domain-first aplicado aquí = modelar el dominio de los datos como SSOT y subir hacia los adaptadores. El contrato y los invariantes están en §3.4; el diseño concreto (esquema, algoritmos, mapeo) en §7.

### 3.6 Comparación con alternativas (solo las que eran candidatas reales)

| Opción | Modelo | Write | Search | Ventajas | Desventajas para Walker |
|---|---|---|---|---|---|
| **`SqliteBackend` (elegido)** | B-tree relacional + WAL; read model en memoria | transaccional O(log N) | ES-like O(1) en memoria (read model); SQL/FTS5 como evolución | **`sqlite3` = stdlib (cero deps)**; transacciones/WAL resuelven delete, atomicidad y crash-recovery; multi-proceso (WAL: N lectores, 1 escritor); fichero único por proyecto = backup simple; embedded | formato binario (sin log plano cateable; export disponible); read model O(N) de carga en Fase 1 |
| `SegmentFileBackend` (JSONL) | segmentos append + snapshot tras compact | O(1) append | ES-like O(1) en memoria | log plano auditable; sin deps | **ingeniería de DB propia**: tombstones/delete durable, compact atómico, recencia multi-fichero, versionado — los bugs A1–A5; compact O(N) con lock |
| LevelDB/RocksDB (LSM) | memtable + SSTables | O(1) append + compaction | range scan; full-text NO nativo | LSM probado, embedded | no grafo; full-text hay que construirlo; binding C (deps) |
| SQLite sin read model (SQL puro) | B-tree relacional | transaccional O(log N) | SQL (+FTS5) | sin carga O(N) en memoria; recencia fresca siempre | cambia la semántica de búsqueda → toca gates (contradice "búsqueda no cambia"); es la evolución §3.7, no la Fase 1 |
| JSON file (actual) | blob único | **O(N) full rewrite** | escaneo lineal | simple | **write amplification (el problema de hoy)** |

**Ventaja del diseño elegido:** conserva el modelo mental que ya funciona (grafo + ES-like, **búsqueda intacta**), elimina la write amplification con transacciones probadas (B-tree+WAL, no con ingeniería de formato propia), es embedded y sin dependencias (stdlib), y la interfaz `StorageDb` deja abierta la evolución a FTS5 o servidor sin tocar el dominio de datos. Costos honestos: formato binario (el trace como evidencia se exporta por SQL si hace falta) y carga O(N) del read model en Fase 1 — acotada por benchmark (CA-5) y retención (ADR-064 S2).

### 3.7 Evolución futura (nota de roadmap — no condiciona el diseño actual)

`StorageDb` es la **costura de evolución**, con dos ramas sobre el mismo puerto:
- **Búsqueda SQL/FTS5 (opción b):** si la carga O(N) del read model deja de caber, el read path pasa a queries SQL indexadas en disco (recencia siempre fresca) — requiere traducir el query dict ES-like → SQL, toca gates; es una decisión aparte, no la Fase 1.
- **Servidor:** un `StorageDbServerBackend` implementaría el MISMO contrato vía JSON-RPC 2.0 (ya el transporte MCP obligatorio), para multi-cliente con ciclo de vida propio. Ambas son roadmap (ver ADR-064), no requisitos de la Fase 1.

---

## 4. `failure_scenarios`

> FS-1/FS-4/FS-5/FS-6 cubren todos los índices (CPG/CKG/Delta/Trace); FS-2/FS-3 son el caso probatorio del trace y el gate.

**FS-1 — Crecimiento del blob sin límite (write amplification)**
- **fallo:** cada evento reescribe N docs; a N grande cada llamada MCP degrada (medido: 57k ≈ 1.3s por rewrite).
- **causa raíz:** formato blob único sin write incremental; ciclo de vida append-only metido en read-modify-write.
- **mitigación:** `SqliteBackend` (transacciones WAL) para todos los índices.
- **detección:** métrica de tiempo de `tools/call` vs tamaño del índice; benchmark con N=50k (CA-3).

**FS-2 — Pérdida de trazas por batching (durabilidad)**
- **fallo:** crash duro pierde hasta `FLUSH_THRESHOLD` eventos del buffer.
- **causa raíz:** mitigación de batching sobre blob; durabilidad sacrificada por performance.
- **mitigación:** commit WAL (`synchronous=NORMAL`) tras cada `trace()` + read model reconstruible; sin batching de aplicación (CA-2).
- **detección:** test que mata el proceso tras N traces y verifica que la BD conserva todos los eventos committed; `has_kg_query` con eventos recién trazados.

**FS-3 — Migración sin read model rompe el gate `required_tools`**
- **fallo:** `has_kg_query`/`replay` devuelven 0 eventos → el gate bloquea submits legítimos.
- **causa raíz:** conflación log/índice: el gate depende del índice para filtrar.
- **mitigación:** read model derivado reconstruido de la BD antes de leer; tests E2E del gate antes de cortar el blob (CA-4).
- **detección:** test E2E del gate `required_tools` con eventos recién insertados; replay con filtro por task/session.

**FS-4 — Degradación super-lineal de CPG/CKG a escala (write amplification latente)**
- **fallo:** builds grandes (miles de nodos) reescriben el blob completo; el build tarda cada vez más.
- **causa raíz:** `replace_blob`/`store_many` amortizan pero no eliminan el O(N); el formato no escala.
- **mitigación:** `replace_blob` atómico (DELETE+INSERT transaccional) y `store_many` upsert sobre SQLite; sharding por proyecto/índice si N explota.
- **detección:** benchmark de build vs N nodos; alerta si el tiempo crece super-lineal.

**FS-5 — Corrupción/truncado de la base sqlite**
- **fallo:** `cognitive.sqlite3` corrupto o truncado (crash durante checkpoint, disco dañado) → error de integridad.
- **causa raíz:** fichero binario único como store durable.
- **mitigación:** WAL (recuperación transaccional al abrir), `PRAGMA integrity_check` en load, backup periódico del fichero único (cp consistente tras checkpoint), fallo **ruidoso** con instrucción de restauración.
- **detección:** error al abrir/consultar → fail ruidoso (nunca datos stale en silencio); test: truncar el fichero y verificar detección.

**FS-6 — Falla del import de migración (json.gz → sqlite)**
- **fallo:** el import deja la BD parcial (mitad de los índices) o falla a mitad del 57k de trace.
- **causa raíz:** conversión en caliente sobre datos legacy sin punto de rollback.
- **mitigación:** import **transaccional por índice** (todo-o-nada por índice), doble escritura hasta validar gates, backup del `.json.gz` previo al cutover (§7.7).
- **detección:** conteo de docs sqlite == conteo del `.json.gz` por índice tras el import + gates E2E verdes (CA-6).

**FS-7 — Fallo silencioso o retorno vacío ambiguo (IV-6/IV-7)**
- **fallo:** una búsqueda falla y devuelve `{}`/`None` que el invocante interpreta como "sin datos" (p.ej. `has_kg_query` pierde un kg_query en silencio).
- **causa raíz:** puerto con retornos ambiguos o `except` silencioso.
- **mitigación:** errores custom por fase (§6.1) como `failure(E)` + shapes explícitos de sin-hits (`{"hits": [], "total": 0}`); prohibidas las ramas `except: pass` (IV-9/CA-10).
- **detección:** test por cada error custom (un test por fase) y cobertura de ramas de fallo obligatoria (CA-10).

---

## 5. Checklist de segundo orden (AGENTS.md, referencial)

- ¿Qué módulos importan esto? → `trace_log.py`, `cognitive_*_repo.py`, `file_backend.py`, `ports.py`, gates.
- ¿Qué side effects dispara? → escrituras transaccionales a sqlite (WAL); reconstrucción del read model.
- ¿Qué eventos de dominio publica? → eventos trace (kg_query, ToolRegistered/ToolInvoked, WORKFLOW_FORCED).
- ¿Qué tests dependen? → gates `required_tools`, E2E BDD, tests de completitud de registries, y los ~10 tests con fixtures `.json.gz` directos (CA-9, §6.5-H4/§6.6).
- ¿Qué cambia en MCP/CLI/CKG/Delta/Trace? → el adaptador MCP y CLI consumen la misma API de `TraceLogger` (sin cambio de contrato); CKG/Delta no cambian en Fase 1.
- ¿Qué deps/archivos toca? → solo stdlib `sqlite3` (cero deps nuevas); barrido de dead code post-refactor (IV-10/CA-10): `FileBackend` eliminado, `file_io` solo refs/export, 6 lectores directos retirados.

---

## 6. Impacto, criterios de aceptación y versionado de formato

### 6.1 Fases e impacto

**Fase 1 (infraestructura): `SqliteBackend` (transacciones WAL) en la capa de ficheros** — beneficia a CPG, CKG, Delta-specs y Trace; el batching del `TraceLogger` se revierte; migración de los blobs existentes (§7.7). Impactos esperados, medidos contra el código (§6.5):

- **Dominio de negocio (capas 0–1):** **cero cambios** — entidades (TraceEvent, Spec, facts CKG, Node/Edge CPG) y casos de uso intactos.
- **Repos de negocio (capa 2):** API intacta (consumen la facade). El `TraceLogger` pierde el batching (buffer, `_pending`, umbral 20, `atexit` — ~80 líneas) y vuelve a append por evento; `cognitive_cpg/ckg/delta_repo` **no cambian**.
- **Puertos (capa 3):** `StorageBackend` (24 métodos) se eleva a `StorageDb` = 24 + `append`/`compact`/`flush` (27, §7.1); `compact`/`flush` no-op en SQLite.
- **Adaptadores (capa 4):** `FileBackend` → `SqliteBackend` (27 métodos); `Index` pierde `_load`/`_save` y queda como read model puro (refactor P7); `file_io` queda para refs/export; layout nuevo `cognitive.sqlite3` + WAL (§7.2).
- **Composition root (capa 5):** `memory.backend="sqlite"` en `config.py`; inyección en `bootstrap.py:267` y `composition/registry.py:340`.
- **Puntos de contacto con el formato (CA-9, §6.6):** 6 módulos de features migrados a consulta por facade; 10 tests a fixtures vía `SqliteBackend`; `test_file_backend_bdd` (formato IDÉNTICO) pasa a test de export; refs `cpg.ref.*` declarados legacy read-only (no migran); ODM deja de inyectar `storage.path`.
- **Trace (H2):** se migran el blob central y el trace del proyecto real; los traces por sprint se migran o se declaran legacy explícito (§7.7 paso 1).
- **Ganancia esperada:** sin write amplification (IV-1: rewrite O(N) → INSERT O(log N)); durabilidad crash-safe sin buffers de aplicación (IV-2: CA-2/CA-3); búsqueda ES-like intacta sobre read model (IV-3: CA-4); multi-proceso WAL (CA-8); tamaño acotado por retención + VACUUM (IV-5: CA-5).

**Limpiezas de Fase 1 (código que se elimina o degrada):**
- `trace_log.py`: se elimina el batching completo (buffer `_pending`, `_FLUSH_THRESHOLD`=20, `atexit.register`, flush previo a `has_kg_query`/`replay`) → write por evento (~80 líneas).
- `index.py`: se retiran `_load`/`_save`, detección de stale por `_file_mtime`, tmp+rename+fsync y locks de fichero; `Index` queda como read model puro (~200 líneas de persistencia; refactor P7).
- `file_io.read_blob`/`write_blob`: dejan de usarse para índices activos (quedan solo para refs/export/migración).
- `file_backend.py` (`FileBackend`): degradado a legacy y eliminado tras el cutover.
- Lectores directos de features (6 módulos): retirada de la lectura directa de `.json.gz` (CA-9, §6.6).
- Helpers de `path_resolver` (cpg_path/ckg_path/trace_path): solo durante la convivencia; retirada post-cutover.
- `odm/entity.py:128`: eliminada la inyección `"storage": {"path": ...}` (contradice ADR-061 F3).
- Blobs `.json.gz` legacy + `cpg.meta.json`: retirados tras validación post-cutover (conservados como backup).
- `_store_core` (doble `_load` de hoy): desaparece (P5 resuelto).

**Invariantes de Fase 1 (además de IV-1..IV-10, §3.4):**
- Sin retroceso del write path: tras el cutover no existe `store` que reescriba el índice completo (IV-1, CA-1).
- Trace sin batching: cada `trace()` = 1 append + COMMIT (IV-2, CA-2); ningún buffer se reintroduce. **✓ ejecutado 2026-08-27**: `trace_log.py` sin `_pending`/`_FLUSH_THRESHOLD`/`atexit`/`flush()` (el `flush()` público tampoco existe); el `TraceLogger` persiste vía `facade.append()` (1 write + COMMIT); `replay()`/`has_kg_query` sin flush previo. Test: `test_f1_trace_sin_batching_commit_inmediato` (verifica committed desde otra conexión y la ausencia del buffer).
- Ninguna lectura directa de `.json.gz` tras el cutover (CA-9) — ni en features, ni en tests, ni en gates.
- Integridad del import: `count(sqlite) == count(json.gz)` por índice (FS-6/CA-6) o el import **aborta** (todo-o-nada) — sin degradación.

**Robustez de Fase 1 (IV-6/IV-7):**
- Todo fallo de backend → error custom de Fase 1 (abajo) como `failure(E)`: nunca `{}`/`None` ambiguos, nunca `except` silencioso.
- `search` sin hits → `{"hits": [], "total": 0}` (shape explícito, nunca `{}`); `get` sin doc → `None` DOCUMENTADO por contrato o `failure(StorageReadError)`, sin ambigüedad.
- `PRAGMA integrity_check` con violaciones → `StorageIntegrityError` (FS-5): jamás se sigue leyendo datos posiblemente corruptos.

**Estilo de Fase 1 (IV-8):** `SqliteBackend` se implementa con `returns` (`Result[T, E]`) y funciones puras/casi-puras; sin estado mutable global — el read model se reconstruye por la regla de refresh explícita (§6.3a): mismo estado committed + inputs → mismo resultado.

**Prohibido en Fase 1 (IV-9):** fallbacks a `.json.gz` si sqlite falla; modos "compat/legacy" activables en runtime; la doble escritura (§7.7) es una fase TEMPORAL de migración con retirada obligatoria, **NO** un fallback operativo.

**Errores custom de Fase 1 (dominio de datos):**
- `StorageWriteError` — INSERT/COMMIT falló: el evento NO se pierde en silencio (log ruidoso + propagación, IV-6).
- `StorageReadError` — SELECT/reconstrucción del read model falló.
- `StorageIntegrityError` — `integrity_check` con violaciones (FS-5).
- `StorageSchemaVersionError` — `schema_version` > soportado o BD v2 leída por lector v1 (guardas §6.4: fail ruidoso, nunca datos stale).
- `StorageMigrationCountMismatchError` — import del índice no cuadra (`count` mismatch, FS-6/CA-6): aborta ese índice (todo-o-nada), sin degradación.
- `StorageContractError` — método del puerto invocado sin contrato válido (índice no registrado, query no soportado).

**Limpieza post-Fase 1 (IV-10/CA-10):** `FileBackend` se **elimina** (no se "mantiene como legacy"); `file_io` queda solo para refs/export/migración; los 6 lectores directos retirados; los 10 tests sin fixtures `.json.gz`; `odm/entity.py` sin `storage.path`; barrido de imports huérfanos y deps — única adición: stdlib `sqlite3` (cero deps nuevas).

**Checklist de aceptación de Fase 1** (todo marcado antes de declarar la fase "hecha"):

- **Criterios medibles (CA-1..CA-10, §6.2):**
  - [ ] CA-1..CA-10 en verde (append sin write amplification, crash, benchmark, gates, retención, migración, regresión, multi-proceso, formato, limpieza).
- **Calidad del código:**
  - [ ] `SqliteBackend` implementa los **27/27 métodos** del puerto sin `pass`/`except` vacíos ni `TODO`s pendientes (IV-10).
  - [ ] Funciones puras/casi-puras con `returns` (`Result[T, E]`); sin estado mutable global (IV-8); lint y type-check en verde.
  - [ ] Cobertura de ramas de error: cada error custom de Fase 1 con su test (CA-10).
- **Mantenimiento:**
  - [ ] `FileBackend` eliminado (no "legacy en runtime"); `file_io` solo refs/export/migración (IV-9/IV-10).
  - [ ] 0 archivos sin importar (dead code); 0 deps nuevas (solo stdlib `sqlite3`) (IV-10).
  - [ ] 6 lectores directos retirados y 10 tests sin fixtures `.json.gz` (CA-9); layout §7.2/esquema §7.3 documentados al día.
- **Fallos silenciosos:**
  - [ ] Ningún `except: pass` ni `return {}`/`None` ambiguo en el path crítico (IV-6/IV-7) — verificado por grep y tests.
  - [ ] Guardas de lector (§6.4) lanzan `StorageSchemaVersionError` ruidoso — con test.
  - [ ] `count` mismatch del import → `StorageMigrationCountMismatchError` aborta el índice (FS-6), jamás degradación.
- **Violaciones de convenciones:**
  - [ ] Domain-first respetado: `shared/core/cognitive_graph` no importa `features/` (AGENTS.md).
  - [ ] Todo el puerto retorna `Result[T, E]` de `returns` (convención del repo); sin excepciones "suelta" en el contrato.
  - [ ] Sin formatos/protocolos propios nuevos; JSON-RPC 2.0 intacto (fuera de Fase 2 servidor).
  - [ ] Test de completitud para `SqliteBackend` (registries/bootstrap, AGENTS.md).
- **Métricas de fase (project.md → "Métricas de calidad por fase" — obligatorio):**
  - [ ] M-01 (escritura): todo write transaccional + COMMIT sincrónico WAL; audit/eventos POST-commit; errores custom con log — anti-patrón a evitar: Transaction Script (secuencia sin transacción) y Hidden Dependency.
  - [ ] M-02 (estrategias): SSOT del storage = `backends` registry (file/sqlite) con precedencia env > conn > default; sin umbrales mágicos — anti-patrón a evitar: Shotgun Surgery (cambiar de backend tocando N módulos) y Duplicate Code (lógica de ruta duplicada entre config y facade).
  - [ ] M-03 (dominio): entidades/repos sin conocimiento de SQL/tablas (IV-4); import idempotente (mismo blob → mismo estado) — anti-patrón a evitar: Anemic Domain Model y Boolean Trap (flags booleanos de modo).
  - [ ] M-05 (concurrencia): transacción explícita por operación; CA-8 multi-proceso WAL sin locks de fichero — anti-patrón a evitar: Magic Numbers (timeouts/retries mágicos) y God Class (SqliteBackend haciendo todo).
  - [ ] M-06 (configuración): switch de backend por config externa sin tocar dominio ni facade; sin reimplementar lo existente — anti-patrón a evitar: Not Invented Here y Accidental Complexity.

**Fase 2 (opcional, roadmap — no bloquea Fase 1; misma API de dominio):**

| Impacto | Búsqueda SQL/FTS5 (§3.7 opción b) | Servidor (`StorageDbServerBackend`) |
|---|---|---|
| Qué cambia | read path: query dict ES-like → SQL indexado; recencia siempre fresca (sin read model en RAM) | el puerto se implementa vía JSON-RPC 2.0 (transporte MCP obligatorio) |
| Dominio negocio (0–1) | cero cambios | cero cambios |
| Repos (capa 2) | sin cambios de API | sin cambios de API |
| Gates | **toca `required_tools`/`has_kg_query`** (semántica de búsqueda cambia) — re-test E2E (CA-4) | sin cambios de gates |
| Search handler | se reescribe (traductor ES-like → SQL) + tests de búsqueda | igual que Fase 1 (delegado al backend) |
| Ciclo de vida | requiere migración del read model (carga O(N) inicial igual que Fase 1) | proceso propio + multi-cliente; MCP adapter podría consumirlo |
| Puerto | sin cambios de contrato | sin cambios de contrato (mismo `StorageDb`) |

**Limpiezas de Fase 2:**
- Búsqueda SQL/FTS5: el search handler custom del trace (structured scan + recarga del blob por query, H3) se **reemplaza por SQL**; el read model en RAM desaparece del read path (recencia fresca); el traductor ES-like→SQL sustituye los handlers custom. Los gates re-testeados (CA-4) pasan a SQL como fuente de verdad.
- Servidor: `FileBackend` legacy se retira definitivamente; `memory.backend` evoluciona file→sqlite→server (config).

**Invariantes de Fase 2 (además de IV-1..IV-10, §3.4):**
- Búsqueda SQL/FTS5: el traductor ES-like→SQL es **TOTAL** — todo query expresable hoy se traduce o falla con `StorageSearchUnsupportedError`; nunca query tácito ni degradación a scan en memoria.
- Recencia: la fuente de verdad es la BD committed (sin read model en el read path); gates re-testeados (CA-4).
- Servidor: mismo contrato (27 métodos) vía JSON-RPC 2.0; errores de transporte/protocolo → `StorageServerError`, nunca timeouts silenciosos.

**Robustez/estilo de Fase 2 (IV-6..IV-8):** todo query no traducible → `StorageSearchUnsupportedError` (fail ruidoso, jamás scan a mano); implementación con `returns` y determinismo idéntico a Fase 1.

**Prohibido en Fase 2 (IV-9):** modo híbrido "SQL si puede, memoria si no" — el traductor es total o falla; los search handlers custom del trace se **reemplazan**, no se versionan ni se mantienen.

**Errores custom de Fase 2:**
- `StorageSearchUnsupportedError` — query dict ES-like no traducible a SQL (FTS5).
- `StorageServerError` — fallo de transporte/protocolo JSON-RPC 2.0 del `StorageDbServerBackend` (timeout, payload inválido, método desconocido).

**Limpieza post-Fase 2 (IV-10):** search handler custom del trace **eliminado** (reemplazado por SQL); `FileBackend` retirado definitivamente; `memory.backend` file→sqlite→server en config; sin archivos ni deps muertas.

**Checklist de aceptación de Fase 2:**

- **Calidad del código:**
  - [ ] Traductor ES-like→SQL implementado con `returns`, **total** (todo query expresable hoy se traduce o `StorageSearchUnsupportedError`); un test por tipo de query dict.
  - [ ] Sin ramas muertas ni `pass`/`except` vacíos; lint y type-check en verde; gates re-testeados (CA-4).
- **Mantenimiento:**
  - [ ] Search handler custom del trace eliminado (no versionado); `FileBackend` retirado; `memory.backend` file→sqlite→server (config).
  - [ ] Sin deps nuevas (FTS5 nativo si aplica); 0 archivos muertos.
- **Fallos silenciosos:**
  - [ ] Ningún query degradado a scan en memoria (IV-9); `StorageSearchUnsupportedError` propagado, nunca caída tácita.
  - [ ] Servidor: timeout/payload inválido/método desconocido → `StorageServerError` propaga (IV-6); sin timeouts silenciosos.
- **Violaciones de convenciones:**
  - [ ] Mismo contrato (27 métodos) en ambas ramas (IV-3/§3.7); JSON-RPC 2.0 como transporte del servidor (INV del repo).
  - [ ] Domain-first intacto: los dominios siguen consumiendo solo la facade; SQL/FTS5 vive en el adaptador.
- **Métricas de fase (project.md → "Métricas de calidad por fase" — obligatorio):**
  - [ ] M-04 (eventos): traductor ES-like→SQL **total** (todo query expresable se traduce o `StorageSearchUnsupportedError`); gates re-testeados idempotentes por diseño — anti-patrón a evitar: Eventual Consistency sin safeguards (read model stale sin recarga) y Distributed Big Ball of Mud.
  - [ ] M-05 (concurrencia): servidor con timeout explícito → `StorageServerError` (jamás timeout silencioso) — anti-patrón a evitar: Magic Numbers (timeouts/backoff mágicos).
  - [ ] M-06 (configuración): `memory.backend` file→sqlite→server por composición externa; `FileBackend` retirado sin reimplementación — anti-patrón a evitar: Accidental Complexity y Not Invented Here.

- **Otras evoluciones (Fase 2+):** adelgazar el puerto (quitar `compact`/`flush` no-op, §6.3d) y retención real del trace (ADR-064 S2).
- **Limpiezas Fase 2+:** purga de backups `.json.gz` post-cutover tras validación; migración o retirada definitiva de los refs `cpg.ref.*` legacy (read-only desde Fase 1).
- **Cierre (IV-10):** verificación final — 0 archivos sin importar, 0 deps innecesarias, cobertura de ramas de error (cada error custom con su test), puerto 27/27 implementado sin `pass`/`except` vacíos.

**Checklist de aceptación de Fase 2+:**

- **Calidad del código:**
  - [ ] `compact`/`flush` retirados del puerto (§6.3d) sin romper consumidores (grep de usos y tests en verde).
  - [ ] Retención (ADR-064 S2) implementada y calibrada (CA-5); sin código muerto de la era blobs.
- **Mantenimiento:**
  - [ ] Backups `.json.gz` purgados tras validación; refs `cpg.ref.*` migrados o retirados definitivamente.
  - [ ] Auditoría final de imports: 0 archivos sin importar; único storage = `StorageDb` + backends vigentes.
- **Fallos silenciosos:**
  - [ ] Verificación estática: cero `except:`/`except Exception: pass` en `shared/core/cognitive_graph` (grep en CI).
  - [ ] Todo error custom con test; 0 ramas de fallo sin cubrir (coverage estricto).
- **Violaciones de convenciones:**
  - [ ] Invariantes IV-1..IV-10 auditados (checklist §6.1 completo); checklist de segundo orden de AGENTS.md reejecutado y en verde.
  - [ ] Dominios (CPG/CKG/Delta/Trace) sin conocimiento de storage (IV-4) tras todas las fases — verificado por imports.

**Sin cambio de contrato de dominio en ninguna fase:** `StorageBackend`→`StorageDb` es interno (puerto); `ITracer`, `has_kg_query`, `replay` y el adaptador MCP mantienen su API.

### 6.2 Criterios de aceptación (Fase 1 — qué significa "hecho")

| CA | Invariante | Criterio medible |
|---|---|---|
| CA-1 | IV-1 | Test unitario: N `append` de 1 doc NO reescriben docs ajenos (filas de otros `_id` sin cambios; tamaño de BD no crece O(N) por write) |
| CA-2 | IV-2 | Test de crash: matar el proceso tras N `trace()` → la BD conserva TODOS los eventos committed (sin batching) |
| CA-3 | IV-2/rendimiento | Benchmark N=100k: `append` medio < 10ms (WAL); `tools/call` sin degradación vs N (sin picos) |
| CA-4 | IV-3 | Gates `required_tools`/`has_kg_query` E2E verdes con eventos recién insertados; `replay` con filtro task/session |
| CA-5 | IV-5 | Retención (ADR-064 S2) calibrada → tamaño de BD acotado tras retención; `VACUUM` opcional; carga en frío < 2s con N=100k |
| CA-6 | migración | Import transaccional por índice: conteo sqlite == conteo `.json.gz`; gates verdes en convivencia; reader v1 con BD sqlite falla **ruidoso** (§6.4) |
| CA-7 | regresión | Suite E2E BDD completa en verde (tests de completitud de registries incluidos) |
| CA-8 | multi-proceso | Dos procesos: el lector ve los datos **committed** del escritor (WAL) tras aplicar la regla de refresh del read model (§6.3a) |
| CA-9 | formato/§6.6 | Los 6 módulos de features y 10 tests que leen `.json.gz` directo dejan de hacerlo (vía puerto o export) **antes** del cutover; `test_file_backend_bdd` (formato IDÉNTICO) pasa a test de export o se retira con la doble escritura |
| CA-10 | IV-6..IV-10 | Post-refactor: 0 fallos silenciosos/retornos vacíos ambiguos (cada error custom de §6.1 con su test), 0 archivos sin importar, 0 deps nuevas (solo stdlib `sqlite3`), puerto 27/27 implementado sin `pass`/`except` vacíos ni fallbacks a `.json.gz` |

### 6.3 Pendientes de diseño (Fase 1, antes del cutover)

- **a) Recencia del read model en memoria (CA-8):** SQLite resuelve la frescura *committed* entre procesos (WAL), pero el read model vive en RAM — falta la regla de refresh: cuándo recargar (mtime del fichero sqlite / contador de checkpoint en `meta`) y el test de dos procesos. Sustituye al problema de "N segmentos + meta.json" del diseño de ficheros.
- **b) Retención del trace (CA-5):** se DELEGA a ADR-064 (S2). Fase 1 NO descarta eventos; el `VACUUM` se calibra tras tener retención.
- **c) Lectores legacy (CA-6/§6.4):** coexistencia de formatos v1 (blobs `.json.gz`) y v2 (sqlite) durante la convivencia; lectores antiguos fallan ruidoso ante sqlite, no leen stale en silencio.
- **d) Adelgazar el puerto (B2):** `compact`/`flush` quedan como no-op en `SqliteBackend`; eliminarlos del contrato es una revisión futura del puerto (no bloquea Fase 1).

### 6.4 Versionado de formato (`schema_version` + guardas de lector)

- La tabla `meta` (en sqlite) guarda **`schema_version`**: v1 = blobs `.json.gz` (legacy); v2 = sqlite (actual).
- **Guarda de lector 1:** si `schema_version` > versión soportada → **fail ruidoso** `StorageSchemaVersionError` (nunca datos stale en silencio, IV-6).
- **Guarda de lector 2:** si existe `cognitive.sqlite3` y el lector no soporta v2 → **fail ruidoso** `StorageSchemaVersionError`.
- La migración (§7.7) escribe ambos formatos durante la convivencia; los blobs `.json.gz` se conservan como backup hasta la validación post-cutover (CA-6). La doble escritura es TEMPORAL (IV-9): no es un fallback; se retira en el cutover y NO existe ningún camino de lectura v1 post-cutover.

### 6.5 Impacto sobre capas y entidades (medido a fecha 2026-08-27, sobre el código real)

Medición hecha sobre `ports.py`, `index.py`, `__init__.py`, `trace_log.py`, `config.py`, repos y tests (no sobre el ADR). Conclusión: **cero impacto en entidades/capas de dominio de negocio; impacto localizado en el puerto, el adaptador y los puntos de contacto con el formato.**

**Impacto por entidad de CognitiveGraph**

| Entidad | Ubicación (código) | Impacto |
|---|---|---|
| `IndexSchema` | `index.py:26` | Nulo (config pura, ADR-061 F3) |
| `Index` (god-object, 1101 líneas) | `index.py:57` | **Alto:** la persistencia (`_load`/`_save`, detección de stale por `_file_mtime`, tmp+rename+fsync) sale hacia el backend; `Index` queda como **read model puro en memoria** (refactor P7 inevitable) |
| `TokenIndex`/`IndexEntry` | `models.py:56/13` | Nulo — siguen en memoria. ⚠️ **Solo CPG/CKG/Delta lo tienen (fulltext); el trace NO** (56k docs → ADR-055 lo revirtió) |
| `StorageBackend` (puerto real) | `ports.py:27` | **H1 — 24 métodos reales vs 13 en la primera versión de este ADR.** Corregido en §3.4/§7.1: el contrato elevado conserva los 24 + `append` |
| `MemoryConnection`/`config.py` | `connection.py`; `config.py:12` | Bajo — `memory.backend="file"` con fail-fast ya existe (ADR-061); `memory.backend="sqlite"` es el único cableado |
| Composition roots | `bootstrap.py:267`; `composition/registry.py:340`; facade default `__init__.py:66` | Bajo — inyección/swap de `SqliteBackend` |

**Impacto por capa domain-first**

| Capa | Impacto medido |
|---|---|
| 0–1 Dominio de negocio (TraceEvent, Spec, facts CKG, Node/Edge CPG; casos de uso) | **CERO** — ninguna entidad ni caso de uso cambia |
| 2 Repos de negocio (TraceLogger, cognitive_cpg/ckg/delta_repo) | API intacta (consumen la facade). Único cambio: `trace_log.py` pierde el batching (buffer/`_pending`/umbral 20/atexit — ~80 líneas en 26-47/65-89/244) y vuelve a append por evento |
| 3 Puertos | Contrato corregido (H1): 24 métodos + `append`; `compact`/`flush` no-op |
| 4 Adaptadores | `FileBackend` → `SqliteBackend` (27 métodos); `file_io` queda para refs/export/migración; Índices pierden persistencia propia (P7) |
| 5 Composition root | Mínimo — flipear `memory.backend` + inyección en 2 roots |

**Hallazgos medidos (H2–H6) que la primera versión del ADR no cubría:**

- **H2 — El trace tiene 3 ubicaciones de almacenamiento, no 1.** (a) `trace.json.gz` central del índice `.cognitive`; (b) trace del proyecto real `<repo>/.cognitive/projects/{pid}/data/trace.json.gz` (leído por `_collect_project_native_trace` en `__init__.py:241`); (c) traces **por sprint** `<proj>/data/traces/{sprint_id}.json.gz` (`path_resolver.get_trace_path`). El alcance de migración del trace se define por ubicación (§6.6).
- **H3 — El read path caliente del trace HOY es "recargar el blob por query", no TokenIndex.** `_collect_trace_events` (`__init__.py:215`) llama `idx._load()` en cada búsqueda (O(N) de load por query) + lectura del proyecto real. El "read model en memoria ES-like" del §3.4 **no existe hoy para trace** (su búsqueda es structured scan con filtros). La ganancia real de la Fase 1 es "cargar una vez, no por query" (CA-4) — es la mejora a medir, no TokenIndex.
- **H4 — ~16 puntos de contacto con el formato fuera del puerto:** 6 módulos de features leen `.json.gz`/`.json` directo (`cognitive_knowledge/application/query.py:43`, `use_cases/check_ckg_health.py:32`, `ckg_service.py:544`, `code_analysis/builder.py:260`, `conventions.py:95`, `cognitive_knowledge/infrastructure/repository.py`) + helpers de `path_resolver` (cpg_path/ckg_path/trace_path) + ~10 tests con fixtures `.json.gz` escritos a mano (`test_file_backend_bdd`, `test_cognitive_graph_bdd`, `test_compact_search_bdd`, `test_required_tools_gate_bdd`, `test_uncaptured_work_gate_bdd`, `test_event_tracing_design_bdd`, `test_reflection_context_bdd`, `test_ckg_batch_ids_regression_bdd`, `test_auto_query_gate_bdd`, `test_smoke_regressions_bdd`) + **refs CPG** (`cpg.ref.*.json.gz`, read-only con search handler que lee gzip por query, `__init__.py:372`) + **ODM** (`odm/entity.py:128` inyecta `"storage": {"path": f"data/{cls.__index__}.json.gz"}` — contradice ADR-061 F3).
- **H5 — `max_key` semántica de secuencia por prefijo.** CKG genera ids secuenciales (`decision-NNN`): `max_key("ckg-log", "decision")` → `("decision-002", 2)`. `SqliteBackend` debe replicarla (`SELECT MAX(id) LIKE 'prefix%'` o contador en `meta`) — comportamiento idéntico, es contrato.
- **H6 — Edges cross-index.** `add_edge` resuelve el índice dueño vía `find_node_index`; los ids están prefijados (`delta:TASK-042` → `cpg:src/auth.py`). La tabla `edges` con columna owner + `src`/`dst` textuales lo soporta; **sin FKs** (`get_related` tolera destinos inexistentes).

### 6.6 Plan de migración de los puntos de contacto con el formato (CA-9)

Categorías de la medición §6.5-H4 y su tratamiento en Fase 1:

| Categoría | Artefactos | Acción |
|---|---|---|
| Lectores directos de features (6 módulos) | `query.py`, `check_ckg_health.py`, `ckg_service.py`, `builder.py`, `conventions.py`, `cognitive_knowledge/infrastructure/repository.py` | Migrar a consulta vía facade/puerto: `check_ckg_health` → `metrics()`/`get_docs()`; `query.py`/`ckg_service` → `cg.get/search`; `builder.py`/helpers `path_resolver` → solo durante convivencia (doble escritura), retirada post-cutover |
| Tests con fixtures `.json.gz` (10) | lista en §6.5-H4 | Fixtures vía `SqliteBackend` (escribir la BD directamente) o vía import §7.7; nada de escribir `.json.gz` a mano |
| Test de formato IDÉNTICO | `test_file_backend_bdd.py:4` (formato .json.gz == v1) | Se convierte en **test de export** (`export_gz() == formato v1`) o se retira junto con la doble escritura |
| Refs CPG legacy | `cpg.ref.*.json.gz` + `schema.storage_path` + handler gzip (`__init__.py:372`) | **NO se migran en Fase 1** — quedan como formato legacy read-only; excepción explícita documentada en el código |
| ODM | `odm/entity.py:128` (inyecta `storage.path` al schema) | Eliminar la inyección (contradice ADR-061 F3) al migrar |

Orden de ejecución: **doble escritura (§7.7) → migrar lectores directos a la facade → retirar lectura directa del formato → retirar blobs `.json.gz` (backup)**. Cada paso validado por CA-6/CA-9 antes del cutover.

---

## 7. Apéndice: diseño detallado de `StorageDb` + `SqliteBackend`

### 7.1 El puerto `StorageDb` (contrato DB-like)

Evolución de `StorageBackend` (ADR-061) con invariantes IV-1..IV-5 (§3.4). El contrato NO cambia por elegir SQLite: es el backend quien lo implementa. **`StorageDb` = `StorageBackend` real (ports.py, 24 métodos, sin cambios) + `append`/`compact`/`flush` (27 total, H1 §6.5).** Nada de lo que consume hoy la facade (`get_related`, `add_edge`, `max_key`, `keys`, `get_docs`, `replace_blob`, `delete_many`...) puede desaparecer.

```python
class StorageDb(Protocol):
    # ── registro ──
    def register_index(self, name: str, schema: Any, search_handler: Any = None) -> None: ...
    # ── writes (IV-1: sin write amplification) ──
    def store(self, index: str, doc_id: str, data: dict) -> dict: ...                  # upsert
    def store_many(self, index: str, docs: dict[str, dict]) -> dict: ...
    def append(self, index: str, docs: dict[str, dict]) -> dict: ...                   # NUEVO: write-side append-only (events/trace)
    def replace_blob(self, index: str, nodes: dict, edges: list, metadata: dict) -> dict: ...  # rebuild atómico (CPG builds)
    def sync_edges(self, index: str, edges: list[dict]) -> dict: ...
    # ── reads (ES-like, IV-3: sobre read model en memoria) ──
    def get(self, index: str, doc_id: str) -> dict | None: ...
    def search(self, index: str, query: dict) -> dict: ...
    def get_docs(self, index: str) -> dict[str, dict]: ...
    def keys(self, index: str) -> list[str]: ...
    def upsert(self, index: str, key: str, data: dict) -> dict: ...
    # ── secuencias (H5: max_key con prefijo, p.ej. CKG decision-NNN) ──
    def max_key(self, index: str, prefix: str = "") -> tuple[str, int] | None: ...
    # ── grafo / edges cross-index (H6: ids prefijados, owner por índice, sin FKs) ──
    def add_edge(self, from_id: str, to_id: str, **attrs: Any) -> dict: ...
    def remove_edge(self, from_id: str, to_id: str) -> dict: ...
    def get_edges(self, node_id: str, direction: str = "both") -> list: ...
    def get_related(self, node_id: str, depth: int = 1) -> dict: ...
    def get_all_edges(self, index: str) -> list[dict]: ...
    def find_node_index(self, node_id: str) -> str | None: ...
    # ── metadata ──
    def get_metadata(self, index: str) -> dict: ...
    def set_metadata(self, index: str, metadata: dict) -> dict: ...
    # ── introspección ──
    def schema_info(self, index: str) -> dict: ...
    def metrics(self, index: str) -> dict: ...
    def list_indexes(self) -> list[str]: ...
    # ── mantenimiento (IV-5; en SqliteBackend: compact≈no-op, flush≈COMMIT) ──
    def delete(self, index: str, doc_id: str) -> dict: ...
    def delete_many(self, index: str, doc_ids: set[str]) -> dict: ...
    def compact(self, index: str) -> dict: ...    # no-op en SQLite (retención + VACUUM)
    def flush(self, index: str) -> dict: ...      # COMMIT explícito
```

**Contrato y errores (IV-6/IV-7/IV-8):** el puerto retorna `Result[T, E]` de `returns` con `E` = errores custom de la fase (§6.1); las excepciones del backend se traducen a `failure(E)`. Nunca se devuelve `{}`/`None` ambiguos: cada método declara su sin-hits explícito (p.ej. `search` → `{"hits": [], "total": 0}`) o su error custom. Ningún método tiene rama `pass`/`except` vacía (CA-10).

### 7.2 Layout en `<data_dir>` del proyecto

```
<data_dir>/
  cognitive.sqlite3       # BD de TODOS los índices (tablas docs, nodes, edges, events, meta)
  cognitive.sqlite3-wal   # WAL (crash-safe; es el "log" durable)
  cognitive.sqlite3-shm   # índice compartido (multi-lector)
  cpg.json.gz / trace.json.gz / ...   # SOLO durante migración (§7.7); luego backup/legacy
  cpg.meta.json / ...                 # legacy hasta cutover
```

### 7.3 Esquema SQL (mapeo 1:1 al dominio de datos)

```sql
docs   (index TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,          -- JSON
        updated_at TEXT, PRIMARY KEY (index, id))                            -- upsert: store/store_many
events (index TEXT NOT NULL, seq INTEGER PRIMARY KEY AUTOINCREMENT,          -- trace: append-only
        id TEXT NOT NULL UNIQUE, data TEXT NOT NULL)                         -- UNIQUE(id): idempotencia
nodes  (index TEXT NOT NULL, id TEXT NOT NULL, attributes TEXT NOT NULL,
        PRIMARY KEY (index, id))
edges  (index TEXT NOT NULL, src TEXT NOT NULL, dst TEXT NOT NULL,
        attributes TEXT, PRIMARY KEY (index, src, dst))
meta   (index TEXT NOT NULL, key TEXT NOT NULL, value TEXT,
        PRIMARY KEY (index, key))        -- schema_version (v2), mtime, migración, umbrales
```

- `events` preserva la semántica de **event log**: append-only, orden por `seq`, inmutabilidad (no se usa UPDATE) — el propósito del trace no depende del formato plano.
- Grafo (`nodes`/`edges`) almacenado relacional y reconstruido a `nx.DiGraph` en memoria (igual que hoy desde el blob).

### 7.4 Algoritmos

**`append(index, docs)` — write-side (IV-1/IV-2):**
```
1. BEGIN IMMEDIATE
2. INSERT INTO docs/events (con _id) por doc                    # O(log N) B-tree
3. mutar índices secundarios EN MEMORIA (read model)            # search sigue en RAM
4. COMMIT → ack al caller (punto de durabilidad; WAL synchronous=NORMAL)
```
- Si el COMMIT falla → **no hay cambio ni en BD ni en memoria** (se revierte la mutación en memoria): imposible la divergencia memoria/disco.
- Idempotencia: `PRIMARY KEY(index, id)` (y `UNIQUE(id)` en events) — reintentos no duplican.

**`_load(index)` — read-side (IV-3):**
```
1. PRAGMA integrity_check (fallo → fail ruidoso, FS-5)
2. SELECT * FROM docs/nodes/edges WHERE index=?
3. reconstruir Index en memoria (nx.DiGraph + _docs + índices secundarios)
```

`search` opera sobre el Index en memoria (`nx.DiGraph` + `_type_index`/`_field_index`/`_token_index`) — **ES-like O(1), idéntico a hoy**, nunca escanea la BD directa en Fase 1.

**`delete(index, ids)` / `replace_blob(index, nodes, edges, metadata)`:**
```
delete:      BEGIN; DELETE FROM docs WHERE index=? AND id IN (...); COMMIT   # real y durable
replace_blob: BEGIN; DELETE FROM docs/nodes/edges WHERE index=?; INSERT ...; COMMIT  # atómico
```

**`max_key(index, prefix)` — secuencias por prefijo (H5, contrato):**
```
SELECT id FROM docs WHERE index=? AND (id = ? OR id LIKE ? || '%') ORDER BY id DESC LIMIT 1
→ ("decision-002", 2)       # misma semántica que hoy (CKG decision-NNN)
alternativa: contador "seq:<prefix>" en meta (solo si el SELECT degenera)
```

**`add_edge(from_id, to_id, attrs)` / `get_related(node_id, depth)` — grafo cross-index (H6):**
```
add_edge:      owner = find_node_index(from_id)      # ids prefijados (delta:TASK-042 → cpg:...)
               INSERT INTO edges (index=owner, src, dst, attrs)      # PK (index, src, dst) → idempotente
               + sincronizar nx.DiGraph del read model en memoria
get_related:   BFS sobre el DiGraph en memoria (idéntico a hoy); destinos ausentes se toleran (sin FKs)
```

**`compact(index)`:** no-op en `SqliteBackend` (no hay segmentos que fusionar); la interfaz se conserva por el puerto. La retención (ADR-064 S2) + `VACUUM` opcional acotan el tamaño (IV-5). Adelgazar el puerto (quitar compact/flush) = revisión futura (§6.3d).

**`flush(index)`:** COMMIT explícito cuando el caller maneja transacciones largas (default: cada opción commitea sola).

**Concurrencia multi-proceso:** WAL — 1 escritor, N lectores de datos committed. Sin locks manuales: serialización de escritura y frescura committed resueltas por SQLite. Pendiente solo la **recencia del read model en memoria** (regla de refresh, §6.3a; test CA-8).

### 7.5 Mapeo al código existente

| Hoy | Con `StorageDb` (`SqliteBackend`) |
|---|---|
| `ports.py` `StorageBackend` | `StorageDb` (+ `append`, `compact`, `flush` — compact/flush no-op en SQLite) |
| `file_backend.py` `FileBackend` | `SqliteBackend` (`sqlite3` stdlib, WAL) |
| `index.py` `_save()` (blob O(N)) | INSERT transaccional (O(log N)) + read model en memoria |
| `index.py` `_load()` (blob completo) | SELECT + reconstrucción del Index en memoria |
| `_store_core` (doble `_load`) | desaparece (P5 resuelto) |
| `delete`/`replace_blob` (no atómicos) | DELETE transaccional / `replace_blob` DELETE+INSERT atómico |
| `cognitive_graph/__init__.py` facade | expone `append`/`compact`/`flush`; **misma API** para dominios |
| `trace_log.py` batching (parche) | vuelve a write por evento (INSERT + COMMIT); **batching revertido** |

### 7.6 Transparencia al dominio (IV-4)

```
Dominios de negocio (CPG/CKG/Delta/Trace)   ← no conocen storage
   │  cg.store / cg.append / cg.search / cg.compact   (facade, API sin cambio)
   ▼
CognitiveGraph facade
   │  delega en el puerto
   ▼
StorageDb  ← contrato + invariantes IV-1..IV-5
   ├── SqliteBackend  (sqlite3 stdlib, WAL; read model en memoria)
   └── (futuro) búsqueda SQL/FTS5 / StorageDbServerBackend  (mismo puerto, §3.7)
```

`TraceLogger`, `cognitive_ckg_repo`, `cpg_repo` **no cambian** — consumen la facade igual que hoy.

### 7.7 Migración (doble escritura → import transaccional → cutover)

1. **Alcance por ubicación (§6.5-H2):** se migran los blobs centrales del índice (cpg/ckg/delta/trace del `.cognitive`) y el trace del proyecto real (`<pid>/data/trace.json.gz`); los **traces por sprint** (`<proj>/data/traces/{sprint_id}.json.gz`) se migran si siguen activos o se declaran legacy explícito (no migrar en silencio).
2. `SqliteBackend` escribe sqlite (v2); los blobs `.json.gz` legacy se mantienen en paralelo (doble escritura) y se respaldan.
3. **Import transaccional por índice** de los datos existentes (57k traces incluidos): todo-o-nada por índice (FS-6); conteo docs sqlite == conteo `.json.gz`.
4. **Migración de puntos de contacto (CA-9, §6.6):** lectores directos de features → consulta por facade/puerto; tests con fixtures `.json.gz` → fixtures vía `SqliteBackend`; test de formato IDÉNTICO → test de export; refs CPG (`cpg.ref.*`) se declaran legacy read-only (no migran en Fase 1); ODM deja de inyectar `storage.path` (ADR-061 F3).
5. Validar gates (`required_tools`/`has_kg_query`) con E2E (CA-4, CA-7) y convivencia CA-6.
6. Cutover: solo sqlite; `schema_version: v2` en `meta` (§6.4); lectores v1 fallan ruidoso; `.json.gz` legacy se conserva como backup hasta validación post-cutover. **La doble escritura es TEMPORAL (IV-9):** deja de escribirse v1 en el cutover; NO es un fallback operativo — tras el cutover, un fallo de sqlite es `StorageWriteError`/`StorageReadError` propagado (ruidoso), jamás re-lectura v1.

## 8. Estado de implementación — prototipo Fase 1+2 (validado 2026-08-27)

Los criterios de validación fueron comprobados tras implementar el prototipo.

**Entregado (código):**
- `shared/core/cognitive_graph/sqlite_backend.py` — `SqliteBackend` (27/27 métodos del contrato §7.1, WAL, delta transaccional en `SqliteIndex._save` con sha1) + `SqliteIndex(Index)` como read model puro.
- `shared/core/cognitive_graph/storage_errors.py` — árbol de errores custom de Fase 1 (§6.1): base + Contract/Write/Read/Integrity/SchemaVersion/Migration/MigrationCountMismatch/EmptyResult.
- `shared/core/cognitive_graph/backends.py` — `register_storage_backend("sqlite", SqliteBackend)` junto a `"file"`: el switch es config-only (`WALKER_MEMORY_BACKEND=sqlite`), sin tocar facade/repos/composition root (ADR-061 F6).
- `shared/core/cognitive_graph/config.py` — `sqlite` como backend **local** (`_LOCAL_BACKENDS`): uri `sqlite://<base_dir>` derivado igual que `file://`; override honrado solo si el esquema coincide; remoto sin uri sigue fail-fast.
- `shared/core/cognitive_graph/__init__.py` (facade) — `backend=None` + `conn` → `resolve_storage_backend(conn)`; sin `conn` → `FileBackend` backward-compatible.

**Validación (tests):** `tests/test_sqlite_backend_fase1_bdd.py` — 12 tests Fase 1 + 3 tests Fase 2 (smoke `TraceLogger` → CognitiveGraph → SQLite con reconstrucción completa). **43 passed** en el paquete {sqlite_backend, memory_config, file_backend, storage_port} incl. `test_memory_config_bdd` actualizado (`available_backends() == ["file", "sqlite"]`). Cobertura: completitud 27/27 (AGENTS.md), CA-1 (1 doc = 1 INSERT vía triggers de auditoría), CA-2/CA-6 (roundtrip y conteos), CA-4 (search structured + replay por task/session), H5 (`max_key` decision-NNN), H6 (edges cross-index), IV-6 (Contract/SchemaVersion/Integrity errors), IV-7 (shape explícito sin-hits), IV-5 (compact/flush no-op), CA-3 (smoke 1000 appends).

**Fase 3 (migración CA-6/§7.7, validada 2026-08-27):** `shared/core/cognitive_graph/migrations.py` — `import_json_gz_to_sqlite()` (import transaccional por índice, todo-o-nada FS-6; parser del formato histórico `{index, schema_hash, docs:[{_id,...}], edges:[{from,to,...}], metadata?}` + variante legacy `nodes`; verificación CA-6 del conteo ANTES del commit; errores `StorageMigrationError`/`StorageMigrationCountMismatchError` con rollback verificado) y `DualWriteBackend` (convivencia §7.7 paso 2: writes a sqlite primario + blob legacy best-effort con `legacy_write_errors` registrado; lecturas SOLO del primario; fallo del primario propagado ruidoso — IV-9). Tests: import real 102 docs/2 edges/metadata → conteos y read model reconstruido; blob corrupto → rollback; _id duplicados → mismatch + rollback; índice no coincidente; dual-write con fallo de secundario/primario. **48 passed** en el paquete completo.

**Hallazgos de implementación (correcciones al diseño):**
1. La columna `index` del esquema §7.3 es **palabra reservada de SQLite** → se escapa con backticks (`` `index` ``) en el DDL y las ~18 queries del backend; la PK `(index, id)` queda intacta.
2. La guarda de integridad (`PRAGMA integrity_check`) debe ejecutarse **antes** de cualquier PRAGMA/lectura: el `DatabaseError` de `journal_mode=WAL` ocurría antes del guard, rompiendo `StorageIntegrityError`.
3. La property `SqliteIndex._db_path` basada en `PRAGMA database_list` se eliminó (no tenía setter y colisionaba con el `db_path` del constructor); ahora el índice guarda su ruta explícita.
4. **Bug pre-existente destapado** (`shared/core/path_resolver/__init__.py`): `get_engine_workflow_dir()` buscaba `core/`+`shared/` (layout pre-reorganización libs/→shared/core/) y subía hasta `/`, donde `parents[0]` lanzaba `IndexError` en **collection de toda la suite**. Corregido a `features/`+`shared/` con `RuntimeError` explícito (ADR-061: cero fallback — el fallback roto contradecía el propio comentario del código).
5. Fidelidad en `events`: el `TraceLogger` persistía vía `store_many` (no `append`), así que la tabla `events` (append-only) se poblaba solo por `backend.append()` — uso previsto para el trace de auditoría directa. **Corregido en el rollout F1 (2026-08-27)**: el `TraceLogger` ahora persiste vía `facade.append()` (modelo de escritor único), y la tabla `events` se puebla en el camino real.
6. No-op `compact`/`flush` verificados (©§6.3d-B2). Pendientes explícitos tras el prototipo: **cutover real** (tejer `import_json_gz_to_sqlite` + `DualWriteBackend` en el composition root sobre los blobs reales del `.cognitive` y proyectos, §7.7 pasos 3-6), **CA-8** refresh multi-proceso del read model (§6.3a) y **retención/VACUUM** (CA-5, ADR-064 S2).

### 8.1 Rollout Fase 1 — limpiezas ejecutadas (2026-08-27)

Primer tramo del rollout: las limpiezas de infraestructura de Fase 1 (§6.1), sin tocar aún features ni el cutover real.

**Entregado (código):**
- `trace_log.py` — **batching revertido** (IV-2): eliminados `_pending`, `_FLUSH_THRESHOLD`=20, `atexit.register`, `_flush_pending()` y el `flush()` público; cada `trace()` = `cg.append("trace", {event_id: doc})` → 1 write + COMMIT inmediato; `replay()`/`has_kg_query` sin flush previo (la evidencia reciente siempre está committed). ~80 líneas retiradas (§6.1).
- `file_backend.py` — **puerto elevado StorageDb (27)**: `FileBackend` implementa `append` (equivalente store_many: persiste en cada write) + `compact`/`flush` no-op documentados, para satisfacer el contrato §7.1 durante la convivencia pre-cutover.
- `__init__.py` (facade) — `append()`/`compact()`/`flush()` públicos que delegan al backend; un backend legacy sin `append` falla con `StorageContractError` (IV-6, nunca silencioso).
- `migrations.py` — `migrate_walker_memories(data_dir, backend)`: **helper de cutover §7.7** que importa los blobs canónicos `cpg/ckg/delta/trace.json.gz` del `data_dir` real de un proyecto (con peer legacy `cog/data/`), transaccional por índice (CA-6/FS-6); blobs ausentes → `imported=False` (un índice puede no existir aún), nunca fallo.

**Validación (tests):** `tests/test_sqlite_backend_fase1_bdd.py` ahora con **24 tests** (12 F1 + 3 F2 + 5 F3 + 4 rollout), **52 passed** en el paquete {sqlite_backend, memory_config, file_backend, storage_port}. Cobertura del rollout:
- `test_f1_trace_sin_batching_commit_inmediato` — sin `_pending`/`_flush_pending`/`flush`; el evento tras `trace()` está committed para OTRA conexión (WAL) sin paso adicional.
- `test_f1_facade_append_contract_error` — facade.append sobre backend sin append → `StorageContractError`.
- `test_ca8_multi_conexion_wal_reader_ve_commits` — **CA-8 (prerequisito multi-proceso)**: escritor + lector sobre la misma BD (`file://`), commits visibles sin locking ni reconstrucción, en ambos sentidos.
- `test_f3_cutover_migrate_walker_memories` — cutover por data_dir: 3 blobs importados (conteos vs BD física, `PRAGMA journal_mode=wal`), trace ausente → `imported=False` sin fallo.

**Verificado:** 15 tests de flujos de trace (`test_trace_side_effect_bdd`, `test_workflow_spring_bdd`, etc.) siguen en verde con el `TraceLogger` sin batching; los 7 fails de `test_event_tracing_design_bdd`/`test_reflection_context_bdd` son **pre-existentes** (refactor ajeno de `features/event_tracing` exige `graph_factory` inyectado desde el composition root — registrado también en §9 como bloqueo de CA-7).

**Pendiente del rollout F1 (no ejecutado aún):**
- **CA-9** — 6 lectores directos de features (`ckg_service`, `query.py`, `check_ckg_health`, `cpg builder/adapters`, `project_service`…) y 10+ tests con fixtures `.json.gz` → facade/puerto (§6.6).
- **Refactor P7** — `index.py`: retirar `_load`/`_save`, stale, tmp+rename+fsync y locks → `Index` read model puro (~200 líneas).
- **Cutover real §7.7 pasos 3-6** — `migrate_walker_memories` + `DualWriteBackend` cableados en el composition root sobre los blobs reales del `.cognitive`/proyectos; validación post-cutover; `FileBackend` degradado y **eliminado** (checklist §6.1: "FileBackend eliminado (no legacy en runtime)").
- **CA-8 completo** — refresh multi-proceso del read model (§6.3a) con el flujo de recarga real.
- **CA-7** — suite E2E completa en verde (bloqueada por el refactor ajeno de `features/main`:: `kg_extract`/`searcher_factory` y `graph_factory` en event_tracing).
- **Fases 2 y 2+** — búsqueda SQL/FTS5, servidor JSON-RPC, retención/VACUUM (CA-5, ADR-064 S2), adelgazar puerto (compact/flush fuera).