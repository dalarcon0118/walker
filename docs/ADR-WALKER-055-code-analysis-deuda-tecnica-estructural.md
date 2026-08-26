# ADR-WALKER-055: code_analysis — deuda técnica estructural y plan de saneamiento

> **Status:** Propuesta
> **Fecha:** 2026-08-24
> **Autor:** Kilo (agente) + PO
> **Complementa:** ADR-WALKER-054 (almacenamiento unificado D4/D5), ADR-WALKER-048 (migración de use cases → application services), ADR-WALKER-053 (composition root / DI), BUG-BACKLOG-001

---

## 1. Problema

Revisión profunda por capas del módulo `features/code_analysis` (2026-08-24, tras la limpieza qlty + ADR-054). Tres niveles de hallazgos:

### 1.1 Lo evidente (síntomas)
- `BuildGraphUseCase` = 1261 líneas / ~10 responsabilidades (god class).
- Duplicaciones: `business_analyzer` ×2 capas activas, dead-code ×2, resolución de paths ×4, `_load_bug_proposals` ×2.
- Readers crudos del CPG: `features/project/infrastructure/watcher.py` (gzip+json directo **fuera** del módulo) y `use_cases/check_cpg_health.py` (raw en capa de aplicación).
- Off-by-one latente en `use_cases/_common.py:_resolve_repo` (`paths.project_root` como data_dir → 0 hits silenciosos).
- Service locator (`get_module_service`) ×8 dentro del módulo.

### 1.2 La estructura
- El port `CpgRepository` es **decorativo**: la ruta principal (`BuildGraphUseCase`) usa `CpgFileRepository` concreto directo.
- **Write path**: `build()` → `_repo.save` ×5 (cada uno = `store_many`+`sync_edges`+`set_metadata`) + `_sync_to_cognitive` + `_persist_quality_report` ×3 ≈ **15-18 escrituras del mismo archivo por build**.
- **Blob poliglot**: el CPG mezcla espejo (`nodes`, `edges`) con derivados (`business_layer`, `quality_report`, `bug_proposals`, `dfg`, `analysis_results`) y metadata (`source_fingerprint`, `stats`).
- Acoplamiento cruzado: `code_analysis → features.project` = 24 imports; `features.project → code_analysis` = 0 imports **pero parsea su archivo** (acoplamiento por formato, sin contrato).

### 1.3 El modelo mental (marco conceptual del framework)
Cada problema es fricción entre el modelo declarado y la implementación:

| Modelo declarado | Fricción real |
|---|---|
| M1 "Cada feature es un bounded context con SSOT propio" (ADR-015/045) | El módulo declara "SINGLE owner del CPG" pero no expone puertos para fingerprint/health → otros features parsean el archivo |
| M2 "El CPG es el espejo del código" (grafo de estructura) | El blob poliglot almacena **derivados como si fueran primarios** Y los recomputa en paralelo (`metrics_service` vs stored) → doble verdad, drift |
| M3 "Escritura única, lectura múltiple" (CQRS) | Dual-write persistente (`_repo.save` + `_sync_to_cognitive`); la migración G3 escribía en `_load()` (endurecido en D5) |
| M4 "El repository es la frontera de IO" | Watcher y CheckCpgHealth parsean crudo porque el repository no expone `read_fingerprint`/`count_nodes` → **ruptura real con D5** (source_fingerprint en metadata) |
| M5 "Migración transitoria" (ADR-048/INV-013) | La migración nunca terminó: monolitho `use_cases.py` murió recién hoy; shims sin fecha de corte → deuda permanente |
| M6 "El composition root resuelve" (ADR-052/053) | Service locator desde capas internas → acoplamiento oculto al container, testeabilidad rota |

**Patrón invisible dominante**: el módulo evolucionó por **parches de bugs** (comentarios "bug:", "FIX") en vez de por refinamiento del modelo → acumulación de dual-write, readers crudos y shims.

---

## 2. Decisión

Saneamiento estructural en 7 frentes (F1-F7), en orden de impacto/riesgo:

### F1 — Exponer port de lectura del CPG para el framework (cerrar readers crudos)
Nuevo puerto de salida en `application/ports/` (o `domain/services/analysis_service.py`): `CpgReader` con `read_fingerprint()`, `count_nodes()`, `load_docs()`. Implementado por `CpgFileRepository` (adaptador del índice). `features/project/watcher.py` y `use_cases/check_cpg_health.py` pasan a usarlo — **se elimina el parseo crudo del archivo fuera del módulo**.

### F2 — Unificar el save del índice (single write por build)
`_repo.save(data)` → **una sola escritura** del índice por invocación: nuevo método `save_graph_blob` en `CpgFileRepository` que haga `store_many`+`sync_edges`+`set_metadata` en un solo lock/cycle (o un `Index.save_all`). Eliminar la redundancia `_repo.save` ×5 + `_sync_to_cognitive` (el sync se convierte en el único save del build).

### F3 — Separar los derivados del blob del espejo
`business_layer`, `quality_report`, `bug_proposals`, `dfg` dejan de vivir DENTRO del SSOT gráfico del CPG: pasan a **proyecciones** (archivos `*.json` versionados aparte, ya existen quality_report.json/bug_proposals.json) o a **recomputables con versión** (el `metrics_service` ya los recomputa). El CPG (índice) queda como espejo: nodes+edges+metadata esencial.

### F4 — Hacer efectivo el port `CpgRepository` en la ruta principal
`BuildGraphUseCase` inyecta `CpgRepository` (port) en vez de construir `CpgFileRepository` concreto; la fábrica `cpg_repository()` (builder.py) sigue siendo el único composition point.

### F5 — Cerrar la migración ADR-048 con fecha de corte
Borrar shims sin consumidores (`application/use_cases.py` — ✅ ya eliminado; `entities.py` root, `application/business_analyzer.py` shim — ✅ ya convertido) y migrar los consumidores restantes de use_cases directos a application services. **Fecha de corte: próximo sprint** — después, los shims restantes se borran aunque haya consumidores (se actualizan en el mismo commit).

### F6 — Eliminar service locator de capas internas
`build_service._sync_core`/`_sync_to_cognitive` y repos de infraestructura reciben el `CognitiveGraph` (o una factory) **inyectado** en vez de `get_module_service(...)` interno → testeabilidad sin `compose_app()`.

### F7 — Consolidar derivados duales
`business_analyzer` queda ÚNICAMENTE en `domain/services/` (✅ shim ya re-exporta); `_scan_dead_code` (review_service) y `_collect_bug_proposals_core` (build_service) comparten un solo scanner de dead-code en domain (`domain/services/analysis_service.py`).

### 2.x Allowlist de excepciones

(vacía al aprobar; toda excepción cita issue y fecha de caducidad)

---

## 3. failure_scenarios

| # | Fallo | Causa raíz | Mitigación | Detección |
|---|---|---|---|---|
| 1 | Un cambio de formato del CPG rompe silenciosamente al watcher (CPG stale para siempre) | `features/project` parsea `cpg.json.gz` crudo; sin contrato de tipos | F1: port `CpgReader.read_fingerprint` consumido por el watcher; el formato queda encapsulado | Test de contrato: tras build, watcher.scan() reporta `up_to_date` (no stale) |
| 2 | Los derivados (business_layer/quality/bugs) divergen entre lo almacenado y lo recomputado | Blob poliglot + caminos paralelos de cómputo | F3: derivados = proyecciones/recomputables versionados; el CPG queda solo como espejo | Test de coherencia: `metrics_service` vs `quality_report.json` → mismos totales |
| 3 | Build escribe ~15 veces el mismo archivo (write amplification) | `_repo.save` ×5 + `_sync_to_cognitive` + `_persist_quality_report` ×3 | F2: una sola escritura del índice por build | Contador de `_save()` por build en test (≤ 2) |
| 4 | `BuildGraphUseCase` sigue creciendo y cualquier cambio rompe 10 responsabilidades | God class sin división | F4: port efectivo + split planificado (build/quality/bugs/dfg/sync) | Métrica de líneas/responsabilidades en CI (gate suave) |
| 5 | Shims de migración se vuelven permanentes y ocultan doble fuente de clases | ADR-048 sin fecha de corte | F5: fecha de corte fijada; grep-CI de shims (`application/use_cases.py`, `entities.py` root) | Lint/CI rojo si un shim persiste tras el corte |
| 6 | El service locator en capas internas rompe la testeabilidad (requiere compose_app) | `get_module_service` ×8 en build/repos | F6: inyección del grafo en build_service y repos | Test de build sin `compose_app()` en verde |

---

## 4. Consecuencias

**Positivas**
- Se cierra el cross-module raw read (la clase de bug que rompió con D5) — el framework consume un contrato, no un formato.
- El CPG vuelve a ser "el espejo" (M2): derivados como proyecciones → menos drift y menos write amplification.
- `CpgRepository` deja de ser decorativo (M4): la frontera de IO se ejerce de verdad.
- La migración ADR-048 se cierra con fecha (M5): fin de los shims eternos.

**Negativas / costos aceptados**
- F3 implica migrar consumidores de `data["business_layer"]`/`data["quality_report"]` (status, adapter) a las proyecciones.
- F2 cambia el ciclo de escritura del índice (un solo `save_all`) — requiere tocar `Index` (shared/core) con un `save_all(blob)` atómico.
- F6 toca `build_service` (la ruta más crítica) — coordinar con el sprint activo.
- Eliminar `business_analyzer`/shims toca imports en build_service/query_service (ya shim-migrados).

**Relación**: ejecuta la limpieza ya iniciada en ADR-WALKER-054 (§8 D5: índice único); cierra la deuda de ADR-048; refuerza ADR-053 (DI real vs service locator).

## 5. Ejecutado hoy (pre-requisito del plan)
- ✅ `application/use_cases.py` (monolitho muerto, 284 líneas) eliminado — el paquete `use_cases/` es canónico.
- ✅ `application/business_analyzer.py` → shim que re-exporta `analyze` del dominio (F7 parcial).
- ✅ `_resolve_repo` corregido (`paths.data_dir`, off-by-one).
- ✅ **F1 ejecutado** (con criterio SRP — shared puro):
  - `shared/core/cognitive_graph/io.py` → SOLO `read_index_file` (lectura genérica del formato de índice; **NO conoce cpg** — sin `source_fingerprint` ni metadata interpretada).
  - `CpgFileRepository.read_fingerprint()` + `make_cpg_fingerprint_reader(data_dir)` (code_analysis) → el conocimiento cpg vive en el feature que lo posee.
  - `FileWatcher` recibe `cpg_fingerprint_reader` **inyectado por DI** (default None → sin fingerprint → mirror no verificable); ya no parsea el archivo ni conoce el formato. Call sites de status (handlers, project_use_cases ×2, check_health, prepare ×2, build_service.update) inyectan el reader desde la capa de composición.
  - `CheckCpgHealthUseCase` → usa `CpgFileRepository` (sin parseo crudo en aplicación); shim `use_cases/__init__` sin `_read_cpg_file`.
- ✅ Rebuild del CPG del proyecto walker con el código actual → `metadata.source_fingerprint` persistido (verificado 05bc5f7b…); el watcher deja de reportar stale.
- ✅ **F2 ejecutado**: `Index.replace_blob(nodes, edges, metadata)` — reemplazo completo del índice en UN ciclo lock/read-modify-write + un solo `_save()` (atómico). `CpgFileRepository.save()` y `build_service._sync_core` lo usan. Eliminado el prune manual de `_sync_to_cognitive` (el replace lo cubre — full replace, nodos de archivos borrados no quedan stale). **Escrituras por build: ~23 → 4** (verificado con contador de `Index._save`).
- ✅ Auto-revisión del código de la sesión: bug de `save()` upsert-vs-replace corregido (full replace vía `replace_blob`); anti-patrón de import redundante en `_persist_quality_report` eliminado; guard `isdir` en `resolve_code_paths` (repo_path stale del manifest → fallback code_root).
- ✅ **F3 ejecutado**: `CPG_DERIVED_KEYS = {business_layer, quality_report, bug_proposals, dfg, analysis_results}` excluidos del metadata del índice en `CpgFileRepository.save()` y `_sync_core`. El blob queda como espejo: nodes+edges+metadata esencial (version, stats, source_fingerprint). Los consumidores intactos: `_business_layer_core` recomputa on-the-fly; `code_index_orchestrator` usa el valor de retorno del build; `review_code` lee `bug_proposals.json`.
- ✅ **F3b ejecutado**: `scan_cfg`/`scan_dfg` persisten proyecciones `analysis_results.json`/`dfg.json`; `bug_service` las lee con **fallback on-demand** (`_load_projection` + `_scan_dfg_on_demand` nuevo, espejo de `_scan_cfg_on_demand`). El blob NO contiene derivados de análisis.
- ✅ **F4 ejecutado**: `BuildGraphUseCase` recibe `repo: CpgRepository` (port) **inyectado** — default `CognitiveCpgRepository(data_dir, project_id)`; ya no construye `CpgFileRepository` concreto. Solo usa `load`/`save` (ambos del port). Import de `CpgFileRepository` eliminado de build_service.
- ✅ **F5 ejecutado (shims)**: `entities.py` (root, sin consumidores — dead) y `application/business_analyzer.py` (shim) **eliminados**; `build_service` y `query_service` importan `analyze` directamente del dominio. **Resto de F5 (fecha de corte — próximo sprint)**: migrar consumidores de use_cases directos (facade `__init__`, `builder.py`, `cpg_service.py`, `code_context_service.py`) a application services (INV-013).
- ✅ **F6 ejecutado**: **0 `get_module_service` en code_analysis**. `_sync_core`/`_sync_core_delta` reciben el grafo INYECTADO; `BuildGraphUseCase.__init__` acepta `graph` (default: construcción directa `CognitiveGraph(data_dir)` + `register_cognitive_indexes`); `CognitiveCpgRepository` acepta `graph` (default idem). Build e index funcionan **sin `compose_app()`** (testeabilidad).
- ✅ **F7 ejecutado**: scanner ÚNICO `find_unused_functions` en `domain/services/analysis_service.py` (filtros inyectados: `is_false_positive_dead_code`, `is_referenced`, `main_guard` opcional). `review_service._scan_dead_code` y `build_service._collect_bug_proposals_core` delegan (eliminada la duplicación del walk del grafo). Verificado: `unused_fn` detectada por ambos; usadas excluidas; main-guard respetado. Comportamiento idéntico al original (el CALLS edge espurio del main-guard es imprecisión preexistente del detector ast-grep, no del scanner).
- ✅ **F5 (app services) ejecutado**: **`CodeAnalysisService`** (patrón walker, como CkgService/SpringService) — UN app service que expone los 5 use cases del feature hacia el composition root: `gather_context`, `business_layer`, `detect_unused_functions`, `review_code`, `project_metrics`, `check_conventions`. Eliminados los 5 servicios 1:1 (proliferación). Expuesto en la facade. Bonus: fallback de `_resolve_repo` mejorado (prueba `<chain_dir>/data` antes de usar chain_dir como data_dir).
- ✅ **Delegación Nest-like ejecutada (ADR-052/053)**: `WalkerCpgAnalysisAdapter` (port `ICpgAnalysisPort`) **delega** `gather_context`/`business_layer`/`detect_unused_functions`/`review_code`/`project_metrics` en el **`CodeAnalysisService` inyectado** (el adapter solo traduce el puerto: repo + eventos). `cpg_analysis_adapter(chain_dir)` construye e inyecta el servicio; `"code_analysis"` registrado como provider DI en el composition root (registry.py `register_all`). Eliminada la duplicación de orquestación (el adapter ya no implementa la lógica inline).
- ✅ **R1/R4 (patrones extraídos a shared)**: nuevas primitivas compartidas:
  - **`shared/core/context_resolver.py`** (`MemoryContext` + `resolve_memory_context`) — la ÚNICA aritmética de resolución de `project_id`/`repo_root`/`data_dir` (reemplaza los ~65 wrappers repetidos). Usa `path_resolver` + manifest **local** (NO registry global — evita el bug de descentralizados). Ya cableada en `_common._resolve_repo` y `review_service._resolve_repo_and_data_dir`.
  - **`shared/core/projection_store.py`** (`ProjectionStore`) — lectura/escritura de proyecciones de derivados (fail-open + atómico). Cableada en `bug_service._load_projection` y `build_service._write_projection` (quality_report/bug_proposals/analysis_results/dfg.json).
- ✅ **Migración R3 COMPLETADA (fecha de corte)**: eliminados los monolitos `application/use_cases.py` de **code_analysis** (ya), **cognitive_knowledge** y **project** — los paquetes `use_cases/` (split + shim PEP 562) son la única fuente. Verificado: 0 símbolos faltantes, 0 importadores directos, boot MCP OK (7 proyectos + índices), 26 tests en verde. Los 5 features quedan sin doble fuente de clases.
- ✅ **TraceGraph consultable (arquetipo "índice consultable", multi-proyecto)**: 3 fixes que destraban el tracer del CPG por protocolo MCP:
  1. **`Index._index_doc` resuelve el type por el schema** (`type` → doc field). Antes leía `data.get("type")` crudo → todos los docs del trace caían en `unknown` → queries estructuradas por type devolvían 0. Ahora `type=ToolInvoked → 7.675`, `task_execute → 3.150`, `kg_query → 2.844`, `AnalysisCompleted → 175`.
  2. **`trace_push` persiste `tool` top-level** (antes lo descartaba del payload del hook → trace no consultable por herramienta).
  3. **`kg_search` allowlist incluye los campos del trace** (`tool/session/node/sub_state/source/agent/sprint`) — sin esto, cualquier query estructurada del trace caía en el path federado (`search_many`) → "Indice 'tool' no registrado".
  Además: el **fulltext del trace fue probado y REVERTIDO** (con 56k docs el TokenIndex serializado hace la migración G3 inviable — minutos); el trace se consulta ESTRUCTURADO (índices secundarios O(1)).