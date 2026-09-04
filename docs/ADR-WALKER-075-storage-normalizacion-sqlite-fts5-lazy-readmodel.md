# ADR-WALKER-075: Normalización física del storage sqlite — FTS5, columnas generadas y read model lazy (modelo ES-like en un solo archivo)

> **Status:** Propuesta
> **Fecha:** 2026-09-04
> **Autor:** Kilo (agente) + PO
> **Complementa:** ADR-WALKER-063 (sqlite v2 = SSOT runtime, CA-9), ADR-WALKER-055 (F2/F3 blob reemplazos, derivados fuera del blob), ADR-WALKER-054 (data_dir canónico)
> **Origen:** sesión de remediación de timeouts -32001 (commits `caa49c3`, `63865c0`, `701efe2`, `6e96f58`) + pregunta del PO: "por qué no una estructura por memoria, similar a Elasticsearch; qué tan eficiente/costoso/mantenible es un sqlite con todo mezclado"

---

## 0. Resumen ejecutivo (dominio del problema)

La capa de memorias (cpg/ckg/delta/trace) YA tiene la topología lógica ES-like que el PO propone: **un solo `cognitive.sqlite3` con un índice lógico por memoria** (columna `` `index` `` en tablas compartidas `docs`/`edges`/`meta`/`events`, un `SqliteIndex` wrapper por memoria, schemas `IndexSchema` por índice).

Lo que NO existe es la **normalización física**: cada doc es un **JSON blob en TEXT**, `sqlite_master` tiene **cero índices SQL secundarios**, el fulltext es un **TokenIndex en RAM de Python reconstruido en cada load**, y cualquier query selectiva exige **materializar el índice completo**. La sesión de timeouts demostró el costo en números reales (§1).

**Decisión:** mantener UN archivo sqlite por proyecto (rechazar una BD por memoria) y añadir la **capa de normalización física en 3 fases**: F1 (FTS5 nativo), F2 (columnas generadas + índices SQL), F3 (read model lazy + columna `doc_sha1` + schema v3).

---

## 1. Problema (evidencia medida en la sesión 2026-09-04)

Proyecto real: react-native-frontend — índice `cpg` con **15,805 docs (6.3 MB JSON) + 24,182 edges**.

| # | Síntoma | Causa física | Costo medido |
|---|---|---|---|
| 1.1 | Fulltext mudo tras restart (0 hits con CPG poblado) | `TokenIndex` vive solo en RAM; sin tabla de tokens en BD | bug fixeado a fuerza de rebuild por load (`6e96f58`) |
| 1.2 | Leer UN string (`source_fingerprint`) cargaba el CPG completo | metadata vive en la misma tabla que los blobs → `load()` para todo | ~80K serializaciones por chequeo; minutos de CPU (`63865c0`) |
| 1.3 | Todo acceso re-materializaba el grafo | read model en RAM poblado por `_do_load` completo (json.loads por fila) | 16K+24K filas re-parseadas por operación |
| 1.4 | Queries selectivas = full-scan | **0 índices SQL secundarios** (solo PKs automáticos); filtros en Python | O(total docs) por query |
| 1.5 | Dif de persistencia exigía carga completa | hash solo computable desde el doc en RAM | doble serialización por ciclo (mitigado con hash del raw en `701efe2`, pero el diff SIGUE necesitando el materialize) |

Conclusión: el modelo "blob JSON + Python" escala mal en CPU/GIL para lecturas selectivas y fulltext, y la corrección de cada bug ha sido caro-trazable a la falta de normalización.

---

## 2. Decisión

### D1 — RECHAZO de "una BD por memoria" (4 archivos)

- Los lookups cross-index (`find_node_index`, federación de `agent_context`, cross-refs CPG↔CKG) exigirían `ATTACH` o fan-out a nivel app; el ACID cross-memoria se pierde.
- 4 conexiones, 4 migraciones, 4 backups — **sin ganancia**, porque la ineficiencia no viene del archivo compartido sino de la falta de normalización DENTRO del modelo.
- La tabla compartida con columna `` `index` `` ya da el aislamiento lógico (namespace por memoria).

### D2 — F1: FTS5 nativo (el "inverted index" de ES)

- Tabla FTS5 **external content** por índice con fulltext en schema (`cpg_fts`, `ckg_fts`, ...) sobre `docs` (rowid de sqlite como vínculo).
- Tokenización por índice según `IndexSchema.search.fields` (cpg: `name`, `path`, `signature`, `kind`).
- Sincronización DENTRO de la MISMA transacción del `_save` (insert/update/delete de docs → update de FTS) y resync completo en `replace_blob`.
- Búsqueda: `MATCH` nativo (C-speed, incremental, restart-proof). El path de search de `SqliteIndex` DEJA de tokenizar/filtrar en Python.
- Migración v2→v3 backfill: un pase por índice con fulltext, verificado por conteo.
- `TokenIndex` (RAM) deja de ser el camino de producción (queda para FileBackend/tests no-runtime).

### D3 — F2: columnas generadas + índices SQL para campos calientes

- Columnas **generated VIRTUAL** sobre `docs` para los campos calientes compartidos del modelo: `doc_type` (`$.type`), `doc_name` (`$.name`), `doc_path` (`$.path`), `doc_scope` (`$.scope`).
- Índices SQL: `(index, doc_type)`, `(index, doc_path)`, `(index, doc_name)`, `(index, doc_scope)` — suficientes para cpg (type/path/name), ckg (type/scope), trace/delta (type).
- El router de search despacha filtros estructurados a `WHERE` indexado y **carga solo los docs matcheados** (sin materializar el resto).
- VIRTUAL (no STORED) → cero duplicación de payload; el índice usa el json_extract en write time.

### D4 — F3: read model lazy + columna `doc_sha1` + schema v3

- `docs` gana columna `doc_sha1 TEXT DEFAULT ''` = sha1 del payload crudo (los payloads ya son canónicos `sort_keys` desde `701efe2`).
- `_do_load` por defecto SOLO materializa la metadata + el diff-cache (`id`, `sha1`); los docs se cargan **on-demand** (`get`/`get_docs`/search hits) o vía **`materialize_all(index)`** explícito para consumidores full-graph (scan_cfg, DFG, quality, agent_context).
- Bump `_SCHEMA_VERSION` v2→v3 con guard fail-loud (writers pre-v3 reciben `StorageSchemaVersionError` — INV-016: ningún writer mixto).
- El diff del save deja de requerir el materialize completo (compara `sha1` columnas).

### D5 — Métricas de calidad por fase (AGENTS.md, mínimo 3 por fase)

**Fase F1 (FTS5):**
- [ ] M-01 toda query fulltext resuelve vía `FTS MATCH` (grep: sin tokenización ni filtrado Python en el search path de SqliteIndex) — anti-patrón a evitar: tokenizar en RAM y filtrar docs uno a uno
- [ ] M-02 el update de FTS ocurre en la MISMA transacción del `_save` (y resync completo en `replace_blob`) — anti-patrón: sync fuera de transacción (drift)
- [ ] M-03 recall fulltext tras restart < 50 ms sobre el CPG de 16K docs (benchmark en tests) — anti-patrón: reconstruir el token index en cada load
- [ ] M-04 backfill de migración verificado: `count(docs) == count(fts)` por índice con fulltext — anti-patrón: migración que asume índice vacío

**Fase F2 (columnas generadas + índices):**
- [ ] M-05 los filtros estructurados (type/path/name/scope) usan índice SQL verificado con `EXPLAIN QUERY PLAN` en tests — anti-patrón: full materialize + filtro Python
- [ ] M-06 columnas generadas VIRTUAL (0 duplicación de payload) — anti-patrón: STORED duplicando el JSON en cada fila
- [ ] M-07 los hits cargan SOLO los docs matcheados — anti-patrón: `SELECT *` + filtro en Python
- [ ] M-08 contratos de search parametrizados sobre ambos backends (sqlite=FTS, File=TokenIndex) con semántica idéntica — anti-patrón: tests que solo validan un backend (falsos verdes)

**Fase F3 (lazy + doc_sha1):**
- [ ] M-09 `_do_load` NO materializa docs por defecto (solo id+sha1+meta); `materialize_all()` explícito para consumidores full-graph — anti-patrón: materializar todo implícitamente en cada acceso
- [ ] M-10 `doc_sha1` poblado en INSERT/UPDATE + backfill one-time + sample-verify en `metrics()` — anti-patrón: recomputar hashes con dumps por fila en cada load
- [ ] M-11 re-save sin cambios = 0 writes (test de `701efe2` sigue verde con read model lazy) — anti-patrón: write amplification
- [ ] M-12 schema v3 con guard fail-loud para writers viejos — anti-patrón: writers mixtos sin detección

---

## 3. failure_scenarios

| # | Fallo | Causa raíz | Mitigación | Detección |
|---|---|---|---|---|
| FS-1 | FTS desincronizado: docs escritos pero ausentes/viejos en FTS → búsquedas pierden o devuelven hits muertos | write path que bypasea el delta de `_save`, o rollback parcial que deja FTS tocado sin docs | updates de FTS DENTRO de la transacción única del `_save`; `replace_blob` resincroniza TODO; sin second write path | reconciliación en `metrics()`/`schema_info()`: `count(docs) vs count(fts)` por índice; test de paridad con fixture |
| FS-2 | Migración v2→v3 a medio hacer (DB locked / disco lleno durante ALTER+backfill) → backend no bootea o v3 escrito sin backfill → resultados incorrectos silenciosos | crash a mitad de migración; BD ocupada por otro proceso | ALTER + backfill + write de versión en UNA transacción (DDL sqlite es transaccional); `busy_timeout=10s`; solo se marca v3 tras éxito | guard de versión fail-loud en boot (existente) + `PRAGMA integrity_check` (existente) + conteos pre/post loggeados en la migración |
| FS-3 | `doc_sha1` stale por writer mixto (proceso pre-v3 escribe filas sin actualizar sha) → diff no detecta cambios → CPG stale servido como fresco | proceso viejo corriendo después de la migración | guard de schema version fail-loud: un writer pre-v3 recibe `StorageSchemaVersionError` en boot (nunca escribe) | guard en `_ensure_schema_version` (existente, extiende a v3) + sample-verify: recomputar sha de N filas random vs columna en `metrics()` |
| FS-4 | Consumidor full-graph (DFG/quality/agent_context) recibe datos PARCIALES del read model lazy → análisis incorrectos silenciosos | consumidor olvida llamar `materialize_all()` | API explícita `materialize_all(index)` exigida por análisis de grafo completo; el router solo enruta queries selectivas a SQL; paridad lazy vs eager en tests de integración | tests de paridad (resultados idénticos lazy/eager sobre fixtures) + `stats` del DFG (files_scanned vs total docs) |
| FS-5 | Latencia de escritura regresa (mantenimiento de FTS+índices en cada save) | exceso de índices o columnas STORED duplicando payload | solo campos calientes indexados; columnas VIRTUAL; benchmark de write en CI | benchmark test (write N docs dentro de presupuesto) + throughput en `metrics()` |
| FS-6 | Divergencia de semántica search entre backends (sqlite=FTS vs FileBackend=TokenIndex) → tests verdes con semántica distinta a producción | dos implementaciones de fulltext | contratos de search parametrizados sobre ambos backends con fixture compartido y aserciones idénticas | suite parametrizada (M-08); un contrato que falle en uno falla en CI |

---

## 4. Alternativas consideradas

| Alternativa | Por qué NO |
|---|---|
| Una BD por memoria (cpg.sqlite3, ckg.sqlite3, trace.sqlite3) | lookups cross-index requieren ATTACH/fan-out; ACID cross-memoria perdido; 4× migración/backup; no gana nada sin normalizar (D1) |
| Postgres/DuckDB externo | dependencia de infra fuera del layout canónico por proyecto; el walker debe ser self-contained por proyecto (ADR-063) |
| SQLite JSON1 puro sin columnas generadas (query con `json_extract` on the fly) | sin índice → full-scan igual que hoy; las generated columns son el puente a índices reales |
| Mantener TokenIndex RAM + siempre materialize-all | es el statu quo que produce las clases de bugs 1.1–1.5 (evidencia de la sesión) |

---

## 5. Plan de implementación (fases independientes, tests por fase)

1. **F1 — FTS5**: tabla por índice fulltext + delta-sync en `_save` + resync en `replace_blob` + migración backfill + router de search → `MATCH`. Benchmark de recall en tests (M-01..M-04).
2. **F2 — Generated columns + índices**: ALTER TABLE idempotente (PRAGMA table_info) + índices por campos calientes + router de filtros estructurados a SQL + EXPLAIN en tests (M-05..M-08).
3. **F3 — Lazy + doc_sha1 + v3**: columna sha + backfill + `_do_load` reducido + `materialize_all()` + guard v3 + sample-verify (M-09..M-12).

Cada fase commitea y declara verde su checklist de métricas; sin métricas verdes la fase no se declara completa (AGENTS.md).

---

## 6. Checklist de segundo orden (obligatorio, AGENTS.md)

1. **¿Qué módulos importan esto?** — `SqliteBackend`/`SqliteIndex` (dueños), consumidores: `cognitive_cpg_repo` (load/fingerprint/save), `ckg_read` (search_many), `agent_context` (federación), `build_service` (scan_cfg/DFG/quality = full-graph), handlers de memoria MCP.
2. **¿Qué side effects dispara?** — migración v2→v3 (DDL + backfills, una vez por BD), sync FTS en cada save, columna `doc_sha1`, guard v3 fail-loud para procesos viejos.
3. **¿Qué eventos de dominio publica?** — ninguno nuevo (la tabla `events` conserva su contrato).
4. **¿Qué tests dependen de este comportamiento?** — round-trips de storage existentes (447 verdes), `test_fast_mcp_perf_fixes_bdd.py` (0-rewrites, metadata-only, fulltext), + contratos nuevos por fase (FTS parity, EXPLAIN, lazy/eager, migración idempotente, benchmarks).
5. **¿Qué cambia en MCP adapter, CLI, CKG, DeltaGraph, Trace?** — `recall_knowledge`/`code_search`/`knowledge_query` pasan a FTS MATCH (semántica idéntica, velocidad nativa); CKG/Delta/Trace no cambian su API; el guard v3 fuerza restart limpio de cualquier proceso viejo (intencional, INV-016).

---

## 7. Pre-work ya ejecutado (commits de la sesión)

| Commit | Qué habilita |
|---|---|
| `caa49c3` | poda del walk (el índice ya no infla con node_modules) |
| `63865c0` | `get_metadata_only` (lectura de meta sin materializar — patrón que F3 generaliza) |
| `701efe2` | payloads canónicos `sort_keys` + hash del raw (precondición del `doc_sha1` de F3; 0 dumps en load) |
| `6e96f58` | `_loaded=True` + rebuild una vez (corrige la tormenta de reloads; F1 elimina el rebuild mismo) |
| `8ab011b` | helpers de blobs `.json.gz` deprecados (el modelo físico por-archivo queda marcado muerto) |
