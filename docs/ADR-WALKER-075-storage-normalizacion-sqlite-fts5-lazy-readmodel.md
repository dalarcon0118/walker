# ADR-WALKER-075: Normalización física del storage sqlite — FTS5, columnas generadas y read model lazy (modelo ES-like en un solo archivo)

> **Status:** Propuesta
> **Fecha:** 2026-09-04 (Enmienda 1 post-crítica: parámetros operativos, baseline M0, rollout v3, intenciones ejecutables rule.md, FS-7/FS-8, re-scope F2/edges)
> **Autor:** Kilo (agente) + PO
> **Complementa:** ADR-WALKER-063 (sqlite v2 = SSOT runtime, CA-9), ADR-WALKER-055 (F2/F3 blob reemplazos, derivados fuera del blob), ADR-WALKER-054 (data_dir canónico)
> **Origen:** sesión de remediación de timeouts -32001 (commits `caa49c3`, `63865c0`, `701efe2`, `6e96f58`) + pregunta del PO: "por qué no una estructura por memoria, similar a Elasticsearch; qué tan eficiente/costoso/mantenible es un sqlite con todo mezclado"
> **Calificación pre-enmienda (auto-crítica):** 4.5/10 como plan de implementación — gaps: parámetros sin valores, sin baseline, sin rollout, F2 sub-especificada, edges sin alcance. Esta enmienda los cierra.

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
- **Enmienda 1 (post-crítica) — semántica de ranking:** FTS5 rankea con **bm25**; `TokenIndex` usaba score propio + bigrams. Contrato de paridad: **mismo SET de docs (recall idéntico), orden por bm25** (mejora documentada, NO orden idéntico). Los tests de `top_k` se actualizan a aserciones de set + orden por relevancia FTS; el ADR declara esto como cambio de comportamiento, no fix invisible.
- Migración v2→v3 backfill: un pase por índice con fulltext, verificado por conteo.
- `TokenIndex` (RAM) deja de ser el camino de producción (queda para FileBackend/tests no-runtime).

### D3 — F2: columnas generadas + índices SQL para campos calientes

- Columnas **generated VIRTUAL** sobre `docs` para los campos calientes compartidos del modelo: `doc_type` (`$.type`), `doc_name` (`$.name`), `doc_path` (`$.path`), `doc_scope` (`$.scope`).
- **Enmienda 1 (post-crítica):** las 4 columnas genéricas NO cubren trace/delta → se añaden `doc_event` (`$.event_type`) y `doc_sprint` (`$.sprint_id`), evaluadas a NULL en índices que no las usan. Índices SQL resultantes: `(index, doc_type)`, `(index, doc_path)`, `(index, doc_name)`, `(index, doc_scope)`, `(index, doc_event)`, `(index, doc_sprint)`.
- **Edges (enmienda):** el PK `(index, src, dst)` cubre lookups por `src` (prefijo), pero `get_edges(direction="both")` exige `idx_edges_dst (index, dst)` — índice nuevo, único cambio en `edges`.
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
| FS-7 | Proceso muere entre mutación en RAM y delta en BD → el delta no commitado se pierde | el read model es efímero; la durabilidad vive solo en BD | CONTRATO EXPLÍCITO: el estado en RAM es efímero; el SSOT en reposo es la última transacción commitada. Toda mutación (store/add_edge/delete) dispara `_save` síncrono en el mismo ciclo → la ventana de pérdida = un solo op. Builders multi-op (`replace_blob`, `build`) aceptan la ventana: crash a mitad = BD consistente con el estado ANTERIOR (atomicidad de transacción) | test: kill -9 entre ops → reabrir BD → estado = última transacción commitada (nunca a medias); `PRAGMA integrity_check` en boot (existente) |
| FS-8 | WAL sin techo: readers largos impiden checkpoint → cognitive.sqlite3-wal crece sin límite → disco lleno, escrituras bloqueadas | checkpoints pospuestos por lectores activos; resyncs masivos de FTS en `replace_blob` | `wal_autocheckpoint` default (1000 págs) + `PRAGMA wal_checkpoint(TRUNCATE)` manual tras `replace_blob`/build; presupuesto WAL observable | `metrics()` reporta tamaño de WAL + umbral de alerta; test de crecimiento acotado tras build completo |
| FS-9 | Migración v2→v3 sobre BD SUCIA → backfill de FTS/doc_sha1 contamina con filas huérfanas/blobs muertos → hits muertos, counts desalineados, sha de residuos | migrar sin gate de limpieza (H-1..H-7) | GATE OBLIGATORIO §10.0: `pre_migration_check` falla ruidoso con lista accionable de suciedad; cpg/ckg derivables se reconstruyen post-migración (H-6) | el propio gate (fail-fast); reconciliación post-migración `count(docs)==count(fts)` por índice (M-04) |

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

---

## 8. Parámetros operativos (cierra G1, G3, G4 — Enmienda 1)

Valores CONCRETOS que convierten el ADR de opinión a diseño. Sin estos números el plan no es ejecutable.

| Parámetro | Valor | Fundamento | Dónde vive |
|---|---|---|---|
| `read_model_budget_mb` | **256** | 60–80 MB medidos (16K docs + 24K edges); headroom 3× | `config.json` del walker (`memory.read_model_budget_mb`) |
| `compaction_trigger` | **delta pendiente ≥ 10k ops** O **p99 save > 50 ms** | degrada antes de bloquear | check post-`_save` (contador en RAM) |
| `wal_checkpoint_policy` | auto (1000 págs) + manual `TRUNCATE` post `replace_blob` | evita FS-8 | `SqliteBackend` post-build |
| `fts_backfill_batch` | **2000 filas/pase** | backfill de migración acotado (no bloquea event loop > 100 ms/pase) | migración v3 |
| `retention.trace` | **90 días** (configurable `memory.trace_retention_days`) | traces de sprints cerrados | limpieza en boot |
| `retention.delta` | **∞** (SSOT del sprint — INV: delta_tracking) | el delta NUNCA se poda automáticamente | — |
| `retention.cpg/ckg` | **∞** (espejo del código; el watcher lo invalida) | no aplica TTL | — |
| `benchmark_fixture` | **16K docs sintéticos** (shape cpg real) | fixture determinista; corre en CI **nocturno**, no por PR | `tests/benchmarks/` |

**Anti-patrón a evitar:** parámetro declarado en ADR sin valor, o valor sin el mecanismo que lo hace cumplir (todo parámetro de esta tabla tiene test o check de runtime que lo vigila).

---

## 9. Fase M0 — Baseline obligatorio (cierra G5 — Enmienda 1)

**Regla: ningún umbral de este ADR (M-03, `compaction_trigger`, p99) se ACTIVA sin baseline medido antes.** Umbral sin baseline = opinión.

Protocolo (en una copia real de la BD + fixture sintético):

| Métrica | Cómo se mide | Registro |
|---|---|---|
| `save()` p50/p95/p99 | timer alrededor de `_save` con 1 doc nuevo / 1 edge nueva / 0 cambios (3 casos) | `docs/baselines/storage-v2.md` |
| `load` completo (arranque) | timer de `_do_load` sobre BD real (16K docs) | idem |
| fulltext recall | timer de search `{"text": ...}` con índice poblado (post `6e96f58`, pre FTS) | idem |
| query selectiva | timer de `search {"type": "function"}` materializado | idem |
| WAL growth | bytes de `-wal` tras 1 build completo | idem |

- Baseline commiteado en `docs/baselines/storage-v2.md` ANTES de tocar código.
- Los umbrales de M-03/M-05/FS-5 se **recalibran** contra el baseline: umbral = max(baseline_p99 × 2, valor propuesto) — si el baseline ya cumple el umbral propuesto, el umbral pasa a ser de no-regresión.
- M0 se ejecuta UNA vez y se re-ejecuta solo si cambia el formato de payload (v3).

---

## 10. Protocolo de rollout de esquema v3 (cierra G6 — Enmienda 1)

### 10.0 GATE OBLIGATORIO: estado pre-migración LIMPIO (directiva PO — Enmienda 1)

**La migración v3 SOLO arranca sobre una BD limpia, sin suciedad heredada.** Migrar una BD sucia contamina el backfill (FTS con hits muertos, sha de filas huérfanas, counts desalineados — FS-9).

Checklist de limpieza (TODO verificable por comando):

| # | Check | Cómo se verifica | Si falla |
|---|---|---|---|
| H-1 | Integridad | `PRAGMA integrity_check == ok` | StorageIntegrityError (existente, FS-5) — restaurar backup |
| H-2 | WAL en cero | `PRAGMA wal_checkpoint(TRUNCATE)` → `-wal` ≈ 0 bytes | checkpoint manual; si readers activos lo impiden → abortar migración |
| H-3 | Sin edges huérfanos | todo `(src,dst)` de `edges` existe en `docs` del MISMO índice | borrar edges huérfanos (listado en el error — accionable) |
| H-4 | Catálogo consistente | todo índice de `meta.__indexes__` tiene `schema_hash` y docs ≥ 0 | índice fantasma → registrar o purgar catálogo |
| H-5 | Sin blobs legacy en el data dir | NO existen `*.json.gz` ni `*.json.gz.lock` junto a la BD | **cuarentena** a `<data_dir>/legacy-blobs/` (nunca borrado silencioso — ADR-063) |
| H-6 | Índices derivables RECONSTRUIDOS (opción recomendada) | `cpg`/`ckg` pueden vaciarse y reconstruirse post-migración (`build_code_index`/`extract_knowledge`) — garantiza arranque sin suciedad acumulada | si el PO opta por rebuild: drop de docs/edges del índice derivable ANTES de migrar; delta/trace NUNCA (SSOT — INV delta_tracking) |
| H-7 | Sin estado pendiente | el diff-cache `_saved_docs/_saved_edges` de la BD abierta coincide con lo commitado (0 ops pendientes) | forzar `_save` de reconciliación o abortar |

```text
IF pre_migration_check != ok (H-1..H-7)
THEN
    StorageStateError con la lista ESPECÍFICA de suciedad (accionable)
    NEXT_STATE: operador limpia → re-intenta
ELSE
    ejecutar migración (cadena §10.1)
    NEXT_STATE: BD v3
```

**Distinción clave (H-6):** `cpg`/`ckg` son DERIVABLES del código → el estado más limpio es vaciarlos y reconstruirlos después de migrar el esquema. `delta`/`trace` son SSOT de dominio → NUNCA se vacían; migran con conteos reconciliados. "Vacío sin suciedad" = cero residuo heredado en lo que entra a la migración.

### 10.1 Cadena de migración (existente)

```text
IF la BD se abre con código v3 y meta.__schema__ == v2
THEN
    migrar in situ (lazy): ALTER + backfill FTS/doc_sha1 + set v3
    — UNA transacción, busy_timeout 10s, checkpoint TRUNCATE al final
    NEXT_STATE: BD en v3, writers v2 bloqueados (guard fail-loud)
ELSE IF meta.__schema__ == v3
THEN
    abrir normal
    NEXT_STATE: operación normal
ELSE IF meta.__schema__ desconocida
THEN
    StorageSchemaVersionError (fail-loud, INV-016)
    NEXT_STATE: operador decide (backup/restore o upgrade)
```

- **Checklist de rollout:** (1) detener todo proceso walker con código v2 contra esta BD; (2) primer arranque v3 ejecuta la migración lazy; (3) verificar conteos pre/post (FS-2); (4) los demás procesos arrancan ya en v3.
- **Rollback:** restore del backup de la BD (pre-migración) + revert del código. La migración NO destruye datos (solo añade columnas/FTS); el guard v3 es lo que impide writers mixtos.
- **Migración idempotente:** si el proceso muere a mitad, la transacción hace rollback y el próximo arranque re-intenta (FS-2).

#### Anexo A — Bitácora de ejecución rollout (2026-09-05)

Ejecutado sobre la canónica `.cognitive/projects/walker/data/cognitive.sqlite3` con
`scripts/migrate_canonical_v3.py` (backup vía API de sqlite + boot del runtime =
migración lazy in-situ) y `scripts/verify_canonical_v3.py` (idempotencia + búsquedas).

| Paso | Pre (v2) | Post (v3) | Verificación |
|---|---|---|---|
| schema marker | `v2` | `v3` | marcado SOLO tras éxito (FS-2) |
| docs delta / trace | 8 / 872 | 8 / 872 | **SSOT intacto** (M-04 conteos) |
| edges delta | 7 | 7 | intacto |
| integrity / orphans | ok / 0 | ok / 0 | FS-5 |
| `doc_sha1` | — | 880/880 sin vacíos | M-10: backfill 1 lote (880≤2000) |
| columnas generadas | 0 | 6 (doc_type..doc_sprint) | F2 VIRTUAL (M-06) |
| índices SQL | 0 | 7 (6 `idx_docs_*` por columnas generadas + `idx_edges_dst`) | F2 |
| FTS (view+vt+marker) | — | delta=8 (reconcile 7/7 MATCH), cpg/ckg=0 | F1 M-04 a nivel MATCH |
| events (append-only) | 55 | 55 | log preservado |
| catálogo `__indexes__` | incompleto (M0-1) | **completo**: ckg/cpg/delta/trace | M0-1 cerrado (restauración futura sin re-registro) |

Idempotencia confirmada: re-boot sobre v3 = sin re-migración, FTS `verified` sin
drift (FS-1). Búsquedas reales sobre la canónica: fulltext delta `'trabajo'` → 7
hits (F1); structured delta `{type: 'spring'}` → 1/1 sin materializar (F2/M-07);
fallback seguro del quirk de schema trace (`event_type→'keyword'`) → 0 hits sin
resultados inventados (M-08; quirk pre-existente, pendiente de decisión del PO).
Backup v2: `.cognitive/backups/cognitive.v2.<ts>.sqlite3` (rollback §10.1).
Pendiente del rollout: (1) porción de librería del gate H0 (`pre_migration_check`
como módulo + tests); (2) decisión PO sobre el schema de trace; (3) rebuild de
cpg/ckg derivables en v3 (H-6 opción recomendada del gate).

**Cierre de remediación (plan `docs/plan-remediation-review-adr075.md`, Waves 0-4,
merge fast-forward `16e4c1f..f77d22b` en `adr-065-f1-f6`, 2026-09-05):** los
hallazgos del code review quedaron corregidos por las tareas T-01..T-13.
Pendientes del rollout actualizados: (1) ~~gate H0 como librería~~ → **CERRADO**
(T-04: `shared_kernel/cognitive_graph/pre_migration.py` con `Result`/`StorageStateError`
+ `tests/test_pre_migration_bdd.py`); (2) decisión PO sobre el schema de trace →
**HABILITADA** (T-10: SSOT único `shared_kernel/cognitive_graph/schemas.py::PRODUCTION_INDEX_SCHEMAS`
— el quirk `event_type→'keyword'` ahora se define una sola vez; la corrección es
1 línea + drop/re-backfill de trace); (3) rebuild cpg/ckg derivables (H-6) →
**DESBLOQUEADO** (Wave 2 resolvió los cliffs pre-rebuild: T-05 write path de edges
O(touched), T-06 reconcile de boot barato, T-07 fallback eager visible con warning
+ métrica). H-1 cerrado vía T-12a (CAS por `update_count` en `_save`, decisión
multi-proceso in-contract documentada; BDD multi-conexión en
`test_lazy_readmodel_bdd.py`). Deuda M-5 cerrada (T-09: `metrics()` reporta
`wal_bytes`/`wal_pages` con `pragma_page_size()` real + benchmarks M-03/FS-5 en
`test_benchmarks_bdd.py`). H-2 cerrado (T-08: cero prints en shared_kernel,
meta-test `test_shared_kernel_no_prints_bdd.py`). Suite al cierre: 553 passed
(+56 sobre el baseline de 497).

---

## 11. Implementation intentions (metodología rule.md — contrato ejecutable por agente)

Cada fase como `implementation_intention` ejecutable: IF/THEN/ELSE con precondiciones, resultado esperado y condición de fallo. El agente implementador NO requiere contexto extra fuera de este ADR.

```yaml
implementation_intention:
  id: H0-pre-migration-hygiene
  if: {condition: "BD v2 seleccionada para migración"}
  then:
    action: "ejecutar checklist H-1..H-7 (§10.0): integrity_check, wal_checkpoint(TRUNCATE), edges huérfanos, catálogo, cuarentena blobs legacy, reconciliación de ops pendientes"
  else:
    action: "no-op (gate solo aplica a BD en migración)"
  preconditions: ["ninguna — es la PRIMERA intención del plan"]
  expected_result: "BD limpia (0 suciedad) — precondición de F1"
  failure_condition: "cualquier check H-x falla"
  failure_action: "StorageStateError con lista accionable de suciedad (nunca migrar sucio — FS-9)"
  next_state: "limpio → F1"

  id: F1-fts5
  if: {condition: "schema.has_fulltext() y tabla FTS del índice NO existe"}
  then:
    action: "crear {index}_fts (external content) + backfill por lotes (fts_backfill_batch) + registrar en meta"
  else:
    action: "verificar count(fts)==count(docs) (reconciliación) y abortar si difiere"
  preconditions: ["H0 verde (BD limpia — gate §10.0)", "baseline M0 registrado", "transacción única por lote", "busy_timeout=10s"]
  expected_result: "recall fulltext vía MATCH, restart-proof (M-01..M-04)"
  failure_condition: "count mismatch o timeout de lote"
  failure_action: "rollback transacción + StorageWriteError (nunca partial)"
  next_state: "F1 verde → F2"

  id: F2-generated-columns
  if: {condition: "PRAGMA table_info(docs) sin columna doc_X requerida"}
  then:
    action: "ALTER TABLE ADD COLUMN generated VIRTUAL + CREATE INDEX (index, doc_X) + idx_edges_dst"
  else:
    action: "no-op (columna/índice ya existen — idempotente)"
  preconditions: ["F1 verde", "EXPLAIN QUERY PLAN verificado en test"]
  expected_result: "filtros estructurados resuelven por índice (M-05..M-08)"
  failure_condition: "DDL falla (locked/disco)"
  failure_action: "rollback + StorageSchemaVersionError path (FS-2)"
  next_state: "F2 verde → F3"

  id: F3-lazy-readmodel
  if: {condition: "index marcado lazy y consumidor full-graph solicita datos"}
  then:
    action: "exigir materialize_all(index) explícito; queries selectivas leen por índice"
  else:
    action: "read model eager (comportamiento actual) para índices no migrados"
  preconditions: ["F2 verde", "paridad lazy/eager en tests de integración", "guard v3 activo"]
  expected_result: "_do_load solo id+sha1+meta por defecto (M-09..M-12)"
  failure_condition: "consumidor full-graph recibe datos parciales"
  failure_action: "assert de materialización en consumers full-graph (FS-4) + test de paridad rojo"
  next_state: "F3 verde → TERMINATE (ADR ejecutado)"
```

**Regla de ejecución (rule.md):** cada intención se ejecuta solo si sus precondiciones están verdes; si `failure_condition` se dispara, la fase NO se declara completa y `next_state` vuelve a la fase previa — nunca se salta una fase con checks rojos.

---

## 12. Alcance explícito (lo que este ADR NO hace)

- **Edges sin normalizar** (fuera de scope de F2 salvo `idx_edges_dst`): las traversals de grafo siguen en RAM sobre el read model materializado — la normalización de edges se re-evalúa en un ADR futuro SOLO si `get_related(depth>2)` sobre CPGs >50K docs lo justifica con baseline.
- **F3 no aplica al CPG como consumidor por defecto**: scan_cfg/DFG/quality son full-graph por naturaleza y usarán `materialize_all()` explícito; el lazy beneficia a ckg/trace/delta (consultas puntuales).
