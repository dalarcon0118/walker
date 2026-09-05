# Plan de remediación — hallazgos del code review ADR-WALKER-075

> **Status:** Propuesta (pendiente aprobación PO)
> **Fecha:** 2026-09-05
> **Metodología:** rule.md (ACTION-CENTRIC AGENT WORKFLOW) — fases 0-4 construyen el
> plan; fases 5-9 definen el protocolo de ejecución por tarea.
> **Origen:** code review profesional de F1-F3 + scripts de rollout (memoria #11902).
> **Complementa:** ADR-WALKER-075 (Anexo A, §10-§11). Cierra la deuda declarada del
> Anexo A: (1) gate H0 como librería → T-04; (2) decisión PO trace → habilitada por
> T-10; (3) rebuild cpg/ckg → desbloqueado por Wave 2 (T-05..T-07).

---

## PHASE 0 — OBSERVE

**Facts (verificados contra el código):**

| # | Hallazgo | Evidencia |
|---|---|---|
| H-1 | Diff-cache sin check de generación: proceso B stale borra keys de metadata de A (`DELETE ... NOT IN` RAM) y sobrescribe IDs vía upsert | `sqlite_backend.py:130` ("1 escritor"), `:807-811`, `:744` |
| H-2 | `print()` en código de librería (riesgo de corrupción de stdout si transporte MCP es stdio) | `:954, :1146, :1179, :1196`; grep: cero `logging` en el módulo |
| H-3 | Backup "pre-destructivo" se ejecuta DESPUÉS de purgar H-3 y cuarentenar H-5 | `pre_migration_check.py:98-105` tras `:55-69` y `:91-95` |
| M-1 | `verify_post` aserta membresía, no igualdad → así se coló el "8 índices" (real: 7) | `migrate_canonical_v3.py:154-155`; Anexo A ya corregido |
| M-2 | O(E) `json.dumps` de edges en cada `_save`, cambien o no | `sqlite_backend.py:774-779` |
| M-3 | `ensure_fts` "verified" re-concilia O(docs+terms) en CADA boot | `fts.py:313` vía `sqlite_backend.py:1194` |
| M-4 | Filtro sin columna generada → materialización eager completa (cliff post-rebuild cpg 16K docs) | `structured.py:149-151` → `super().search` |
| M-5 | FS-8 (WAL en `metrics()`) y benchmarks M-03/FS-5 prometidos y ausentes | `:846-871` (sin WAL; page_size hardcodeado `:854`) |
| M-6 | Schemas de producción duplicados en 3 sitios (incl. quirk trace ×3) | `composition.py:334`, `migrate_canonical_v3.py:40`, `m0_baseline.py:71` |
| M-7 | Scripts de verificación sin fail-loud (exit 0 ante FAIL) | `verify_canonical_v3.py:28-29, :33-36` |
| L1..L11 | Nits (hits muertos silenciosos, validadores duplicados, var muerta, etc.) | memoria #11902 |

**Inferencias (no facts):** multi-proceso estaría in-contract (§10.1 lo asume) —
**por validar en T-11 antes de implementar**.
**Suposiciones:** la suite 497 verde es el baseline de no-regresión; la canónica v3
(delta=8/trace=872/edges=7/events=55) es el estado a preservar.
**Contradicciones:** ninguna con el estado anterior.

---

## PHASE 1 — STATE CONSTRUCTION

- **Dónde:** rama `adr-065-f1-f6`; ADR-075 ejecutado y verificado; canónica v3 estable.
- **Problema:** 3 HIGH + 7 MEDIUM + 11 LOW del review sin plan; deuda del Anexo A
  pendiente (gate H0 librería, decisión PO, rebuild derivables).
- **Objetivo activo:** (candidato, ver Phase 2) corregir hallazgos en olas por riesgo.
- **Restricciones:** AGENTS.md — failure_scenarios (≥3), métricas por fase (≥3),
  checklist de segundo orden, fail-fast sin fallbacks, `Result[T,E]`, domain-first;
  SSOT delta/trace NUNCA se tocan; INV-016 sin writers mixtos.
- **Recursos:** módulos localizados con líneas; suite verde 9.25s; scripts de
  verificación; fixture 16K determinista del baseline M0.
- **Información faltante:** ¿el contrato admite múltiples procesos walker por BD?
  (decide fix CAS completo vs invariante documentado — T-11 la resuelve primero).
- **Incertidumbres:** estabilidad de umbrales absolutos de benchmark en CI
  (mitigación: presupuestos relativos al baseline M0).

---

## PHASE 2 — DELIBERATION

**GOAL:** corregir los hallazgos preservando la canónica v3 y el contrato del
ADR-075, en olas ordenadas por dependencia y riesgo: primero los defectos que la
librería H0 heredaría tal cual (Wave 0), luego la librería (Wave 1), luego los
cliffs de performance ANTES del rebuild cpg/ckg (Wave 2), plataforma (Wave 3),
nits (Wave 4).

**DESIDERABILITY:** valor = eliminar vías de corrupción silenciosa (H-1, H-3),
veracidad de la bitácora (M-1, M-7), adherencia a convenciones (H-2); costo ≈ 12
tareas acotadas; riesgo principal = regresión en el write path (mitigación: paridad
CA-1/M-08 en tests + suite completa por tarea).

**FEASIBILITY:** todos los hallazgos tienen fix local conocido; única decisión
externa es la PO sobre trace schema — que T-10 reduce a un cambio de 1 línea.

**ADVERSARIAL VALIDATION:**
- *¿Qué invalidaría la estrategia?* Si el contrato fuese estrictamente mono-proceso
  por BD, H-1 se degrada a "invariante documentado + detección barata" (no CAS).
  → T-11 valida el contrato ANTES de implementar (information gain con valor de
  decisión positivo — no acquisition por defecto).
- *Hipótesis alternativa para M-2:* ¿el O(E) es tolerable si edges solo se crean en
  `replace_blob` (que ya fija `_loaded=True`)? Verificado: NO — el write path
  incremental (`add_edge` suelto, `sync_edges`) paga O(E) por operación; el cliff
  existe y crece con el rebuild.
- *Suposición no validada:* que ningún caller asume el DELETE+INSERT de metadata —
  verificado por grep: el DELETE está aislado en `_save`.

**RULE:** goal deseable ∧ factible ∧ evidencia suficiente → **select_goal → RUBICON**.

---

## PHASE 3 — RUBICON / COMMITMENT

```yaml
commitment:
  status: committed
  goal: ejecutar T-01..T-12 (Waves 0-4) con criterios por tarea y de cierre global
  success_criteria:
    - "Suite completa verde (≥497 tests, sin skips nuevos) tras cada tarea"
    - "Canónica v3 intacta tras Wave 3: delta=8/trace=872, edges=7, events=55 (verify_canonical_v3)"
    - "Gate H0 como librería con tests BDD y StorageStateError (deuda Anexo A #1 cerrada)"
    - "FS-8 + M-03 + FS-5 automatizados (deuda M-5 cerrada)"
    - "PRODUCTION_INDEX_SCHEMAS con SSOT único + test de completitud (M-6 cerrado; decisión PO a 1 línea)"
  assumptions:
    - "Multi-proceso vs mono-proceso se decide en T-11 con evidencia del contrato"
    - "Umbrales de benchmark relativos al baseline M0 si el absoluto es inestable"
  invalidation_conditions:
    - "Una corrección rompe paridad M-08 (semántica de búsqueda) o FS-2 (atomicidad de migración)"
    - "El fix de T-05 requiere reescribir el contrato del grafo (coste > beneficio → re-deliberar)"
```

**RULE:** commitment_conditions_met → **transition_to(IMPLEMENTATION_DESIGN)**.

## PHASE 4 — IMPLEMENTATION DESIGN (tareas = implementation_intentions)

Unidad fundamental: `IF condition THEN action ELSE alternative` (rule.md).
Cada tarea es una implementation_intention con criterio de validación propio.

### Wave 0 — Hotfix de herramientas (la librería H0 heredaría estos defects)

**Rationale:** T-04 convierte `pre_migration_check.py` en librería; los defects de
orden/veracidad se copiarían tal cual. Corregir ANTES de extraer.

#### T-01 · Backup antes de mutación (H-3, L9) · S · HIGH
```yaml
if:   "pre_migration_check.py va a ejecutar remediaciones (purge H-3, cuarentena H-5, drop H-6)"
then: "mover bloque de backup (:98-105) a inmediatamente tras abrir la conexión y ANTES
       del primer DELETE; extensión .sqlite3 (L9a); corregir mensaje stale 'lista para
       M0/F1' (L9b)"
else: "si la BD ya está en v3 (re-run), abortar con StorageStateError — no 'limpiar' una BD migrada"
preconditions: [restore-point vigente de la canónica, suite 497 verde]
expected_result: "conteos docs/edges del backup == pre-estado, verificado antes del primer DELETE"
failure_condition: "conteos del backup ≠ pre-estado O backup creado tras alguna mutación"
failure_action: "restaurar desde .cognitive/backups/, corregir orden, re-ejecutar (fail-fast)"
touches: [scripts/pre_migration_check.py]
validation: "BDD: backup capturado → purgar → restaurar → conteos idénticos al pre-estado"
```

#### T-02 · verify_post por igualdad de sets (M-1, L10) · S · MEDIUM
```yaml
if:   "verify_post() valida columnas generadas e índices SQL"
then: "membresía → IGUALDAD: set(gen_cols) == {doc_type,doc_name,doc_path,doc_scope,doc_event,doc_sprint};
       set(sql_idx) == {idx_docs_type,idx_docs_name,idx_docs_path,idx_docs_scope,
       idx_docs_event,idx_docs_sprint,idx_edges_dst};
       isinstance(backend, SqliteBackend) fail-fast (L10)"
else: "si aparece DDL nuevo legítimo, se actualiza el EXPECTED (completitud, nunca membresía)"
preconditions: [BD v3 real como SSOT: 7 índices (Anexo A ya corregido)]
expected_result: "patrón de completitud de AGENTS.md aplicado — un desvío 8-vs-7 sería imposible"
failure_condition: "cualquier objeto DDL fuera del conjunto esperado"
failure_action: "abortar con listado accionable del desvío"
touches: [scripts/migrate_canonical_v3.py]
validation: "mutación manual del esquema en tmp_path → el assert falla listando exceso/falta"
```

#### T-03 · Verificación fail-loud (M-7) · S · MEDIUM
```yaml
if:   "verify_canonical_v3.py detecta schema != v3, drift de FTS o conteo inesperado"
then: "rama FAIL de idempotencia → assert + sys.exit(1); asserts en el loop
       fts-reconcile (checked == docs por índice)"
else: "n/a — todo check termina en assert o en exit 0 explícito y justificado"
preconditions: [T-02 (mismo estándar de verdad)]
expected_result: "exit-code truthful: un CI no puede dar verde ante drift"
failure_condition: "check fallido con exit 0"
failure_action: "meta-check: el test ejecuta el script contra BD corrupta y exige exit != 0"
touches: [scripts/verify_canonical_v3.py]
validation: "forzar drift en tmp_path → exit != 0; BD sana → exit 0"
```

**next_state(Wave 0) → Wave 1.** Gate: suite verde + BDD backup/restore pasando.


### Wave 1 — Gate H0 como librería (deuda Anexo A #1)

#### T-04 · pre_migration_check como módulo + tests BDD · M · HIGH
```yaml
if:   "un boot/migración necesita validar el estado de storage antes de mutar"
then: "extraer scripts/pre_migration_check.py →
       fast_mcp/shared_kernel/cognitive_graph/pre_migration.py (librería importable);
       checks H0..H-6 como funciones que retornan Result[CheckReport, StorageStateError];
       el script queda como thin CLI sobre la librería; tests BDD: cada check con caso
       ok + caso de fallo forzado"
else: "si un check no puede ser puro (necesita mutar), se separa explícitamente como
       remediation_action con backup obligatorio (hereda T-01)"
preconditions: [T-01, T-02, T-03 (los defects no se heredan)]
expected_result: "deuda Anexo A #1 cerrada: gate invocable desde boot del runtime y CLI"
failure_condition: "check lanza excepción cruda en vez de Result/StorageStateError, o CLI diverge de la librería"
failure_action: "rechazar el merge del gate; el one-shot queda vigente"
touches: [scripts/pre_migration_check.py → shared_kernel/cognitive_graph/pre_migration.py, tests/]
validation: "pytest tests/test_pre_migration_bdd.py: H-0..H-6 con ok + fallo forzado; StorageStateError con remediación en el mensaje"
```

**next_state(Wave 1) → Wave 2.** Nota: T-11 (validación de contrato multi-proceso) es
adquisición de información de riesgo cero — puede ejecutarse EN PARALELO desde aquí.


### Wave 2 — Performance ANTES del rebuild cpg/ckg (gate del H-6)

**Rationale:** tras el rebuild habrá ~16K docs / ~24K edges; M-2/M-3/M-4 pasan de
"irrelevante" a "cliff". Corregirlos primero evita re-trabajo del rebuild.

#### T-05 · Write path de edges O(touched) en vez de O(E) (M-2) · M · HIGH
```yaml
if:   "_save serializa todos los edges del índice para computar el diff (:774-779)"
then: "cachear el json.dumps por edge y serializar solo edges tocados (dirty set);
       el diff compara raw cacheado, no re-serializa"
else: "alternativa mínima si exponer deltas del grafo resulta invasivo: cache por edge
       + recompute on-touch"
preconditions: [tests de paridad edges en verde; fixture 16K del M0]
expected_result: "add_edge individual O(1) en serialización; sync_edges O(delta)"
failure_condition: "divergencia docs↔edges en save/load roundtrip, o edge perdido en tombstone"
failure_action: "revertir a serialización completa + registrar el cliff como deuda explícita en el ADR"
touches: [sqlite_backend.py, tests/test_lazy_readmodel_bdd.py]
validation: "BDD roundtrip add/remove/sync en fixture 16K + spy: dumps por save ≤ touched"
```

#### T-06 · Reconcile de boot barato (M-3) · S · MEDIUM
```yaml
if:   "ensure_fts 'verified' re-concilia O(docs+terms) en cada apertura (:313, :1194)"
then: "verificación barata por defecto (COUNT docs_fts vs docs + total fts5vocab);
       reconcile completo solo si el conteo difiere o docs_at_backfill cambió"
else: "si el conteo barato no detecta un caso real de drift, escalar a sample hash"
preconditions: [independiente de T-05]
expected_result: "boot O(1)-ish en estado sano; drift sigue detectado (el conteo es la primera señal)"
failure_condition: "drift real con conteos iguales (falso negativo) en test de corrupción FTS"
failure_action: "mantener reconcile completo y documentar el coste como aceptado"
touches: [fts.py, sqlite_backend.py, tests/test_fts5_storage_bdd.py]
validation: "BD con término corrupto → boot detecta y reconcilia; BD sana → sin full scan (spy/timer relativo)"
```

#### T-07 · Cliff de fallback post-rebuild (M-4) · M · MEDIUM — DECISION POINT
```yaml
if:   "un filtro estructural usa campo sin columna generada (p.ej. {kind: function} sobre cpg)"
then: "decidir entre (a) columnas generadas para campos de alto tráfico de cpg/ckg en
       el mismo rebuild H-6, o (b) documentar presupuesto eager + warning con conteo de
       docs materializados cuando el fallback dispare"
else: "si PO no decide antes del rebuild, implementar (b) como mínimo (no bloquea; hace visible el coste)"
preconditions: [decisión PO o default (b)]
expected_result: "el coste del fallback es visible (warning + métrica) o eliminado (columna generada)"
failure_condition: "fallback silencioso sin señal — estado actual"
failure_action: "n/a: (b) es el mínimo aceptable; no hay rama que permita silencio"
touches: [structured.py, composition.py, tests/test_structured_sql_bdd.py]
validation: "BDD: filtro no-soportado → warning + métrica de docs materializados; soportado → EXPLAIN usa índice (M-05 ya existe)"
```

**next_state(Wave 2) → Wave 3.** Gate: fixture 16K add/remove bajo presupuesto;
boots sin full reconcile; fallback visible.


### Wave 3 — Plataforma y observabilidad

#### T-08 · Logging en vez de print (H-2) · S · HIGH
```yaml
if:   "el backend escribe progreso con print() (:954, :1146, :1179, :1196)"
then: "logger = logging.getLogger('walker.cognitive_graph.sqlite_backend'); print →
       logger.info/warning conservando los mensajes actuales; sin configuración de
       handlers en librería (delegar al composition root, convención del proyecto)"
else: "n/a — no existe rama que permita escribir a stdout en el shared kernel"
preconditions: [ninguna; 1 archivo]
expected_result: "cero prints en fast_mcp/shared_kernel/** (verificable por grep); canal MCP stdio intacto"
failure_condition: "cualquier print residual en shared_kernel"
failure_action: "agregar meta-test grep-based que falle si reaparece"
touches: [sqlite_backend.py]
validation: "grep -rn 'print(' fast_mcp/shared_kernel/ → 0 resultados + suite verde"
```

#### T-09 · metrics() truthful + benchmarks M-03/FS-5 (M-5) · M · HIGH
```yaml
if:   "metrics() no reporta WAL y usa page_size hardcodeado (:854); M-03/FS-5 sin tests"
then: "usar pragma_page_size() real; añadir wal_bytes/wal_pages a metrics() (FS-8);
       benchmark test: restart → recall < 50ms sobre fixture 16K (M-03) y presupuesto
       de escritura comparado contra baseline M0 (FS-5); si el umbral absoluto es
       inestable en CI, usar presupuesto relativo (≤ 1.5× baseline M0)"
else: "si un benchmark es no determinista en CI, se marca @pytest.mark.benchmark y se
       declara explícitamente en el checklist (nunca se omite en silencio)"
preconditions: [fixture 16K determinista del M0]
expected_result: "deuda M-5 cerrada: FS-8/M-03/FS-5 automatizados o declarados; metrics() sin constantes falsas"
failure_condition: "métrica reportada que no corresponde a la realidad de la BD (p.ej. page_size 4096 fijo)"
failure_action: "revertir a la métrica previa y corregir; nunca envolver en try/except silencioso"
touches: [sqlite_backend.py, tests/test_fts5_storage_bdd.py o nuevo test_benchmarks_bdd.py]
validation: "pytest con el benchmark corriendo en la suite local; página size real verificada en BD con page_size != 4096"
```


#### T-10 · SSOT de schemas de índice (M-6) · S · HIGH
```yaml
if:   "los schemas de producción están duplicados en 3 sitios (composition.py:334,
       migrate_canonical_v3.py:40, m0_baseline.py:71) incl. el quirk trace ×3"
then: "crear fast_mcp/shared_kernel/cognitive_graph/schemas.py con PRODUCTION_INDEX_SCHEMAS
       (dict índice → schema canónico) + test de completitud
       (EXPECTED == set(schemas) — patrón AGENTS.md); los 3 sitios importan de ahí;
       refactor mecánico, cero cambio de comportamiento"
else: "n/a — la duplicación es el defecto; no existe alternativa que la preserve"
preconditions: [ninguna]
expected_result: "quirk trace definido UNA vez; la decisión PO del schema trace queda
       reducida a editar 1 línea + migración"
failure_condition: "algún sitio sigue definiendo su propia copia literal"
failure_action: "meta-test: grep de 'event_type' literal fuera del SSOT debe dar 0"
touches: [composition.py, migrate_canonical_v3.py, m0_baseline.py, nuevo schemas.py, tests/]
validation: "pytest test de completitud + grep meta-test + suite verde"
```

#### T-11 · Validar contrato multi-proceso (H-1, fase previa) · S · INFO
```yaml
if:   "el contrato/ADR-045/INV-016 define si múltiples procesos walker pueden abrir
       la MISMA BD cognitiva simultáneamente"
then: "SI multi-proceso in-contract → T-12a (CAS por update_count);
       SI mono-proceso por diseño → T-12b (invariante explícito + detección barata);
       documentar la decisión como failure_scenario del ADR-075 (nuevo FS-10)"
else: "si el contrato no lo dice → es la información faltante: registrar como
       pregunta al PO ANTES de implementar cualquier fix de concurrencia"
preconditions: [acceso a ADR-045, project.md, historial de diseño]
expected_result: "decisión documentada con evidencia; T-12 tiene alcance definido"
failure_condition: "implementar H-1 sin esta decisión (guessing — viola meta-rule)"
failure_action: "detener y preguntar al PO (phase 13: request_external_input)"
touches: [docs/, project.md, ADR-WALKER-075]
validation: "párrafo de decisión en el ADR con cita del contrato; T-12 actualizado"
```


### Wave 4 — H-1 (según T-11) y nits

#### T-12a · CAS por generación (H-1, si multi-proceso in-contract) · M · HIGH
```yaml
if:   "T-11 concluye multi-proceso in-contract"
then: "meta.update_count como contador de generación: leer en _ensure_light, verificar
       CAS en cada _save (WHERE value = esperado); desajuste → StorageStateError
       'storage cambió bajo el cache — reabrir' (remediación accionable);
       metadata: upsert-por-key en vez de DELETE+INSERT del set completo (cierra la
       vía de borrado barata aunque el CAS falle)"
else: "n/a — rama definida por T-12b"
preconditions: [T-11 con decisión multi-proceso documentada]
expected_result: "dos procesos con caches divergentes: el segundo _save falla con error accionable, nunca corrompe silenciosamente"
failure_condition: "escenario B-stale-borra-k1-de-A reproducible en test"
failure_action: "escalar el diseño: lock exclusivo del backend (no None) en vez de CAS"
touches: [sqlite_backend.py, _acquire_write_lock, tests/test_lazy_readmodel_bdd.py]
validation: "BDD multi-conexión: 2 conexiones al mismo archivo, A escribe, B save stale → StorageStateError; mismo proceso → sin falso positivo"
```

#### T-12b · Invariante + detección barata (H-1, si mono-proceso) · S · MEDIUM
```yaml
if:   "T-11 concluye mono-proceso por diseño"
then: "documentar invariante explícito (INV-nuevo en project.md: 'un writer por BD
       cognitiva'); _acquire_write_lock deja de retornar None: detecta PID/lock file
       y falla con StorageStateError si detecta writer externo; el caso metadata
       (upsert-por-key) se aplica igualmente como hardening barato"
else: "n/a — rama definida por T-12a"
preconditions: [T-11 con decisión mono-proceso documentada]
expected_result: "la suposición '1 escritor' deja de ser un comentario y pasa a ser contrato + detección"
failure_condition: "dos procesos escribiendo sin error observable"
failure_action: "escalar a T-12a (el contrato mono no se sostiene en la práctica)"
touches: [sqlite_backend.py, project.md]
validation: "BDD: segundo proceso/open en modo escritura → StorageStateError con remediación"
```

#### T-13 · Batch de nits (L1, L2, L3, L7, L8, L9, L10, L11) · S · LOW
```yaml
if:   " Waves 0-3 merged y suite verde"
then: "aplicar en UNA PR: L1 log de hit sordo (contador 'fts_hits_sin_doc' en metrics);
       L2 unificar validadores de identificador (uno ASCII, reexportar); L3 derivar
       meta-keys de constante compartida con _page_params; L7 delete_many con @safe
       (contrato Result); L8 eliminar var muerta phs; L9 ya en T-01; L10 ya en T-02;
       L11 collections.abc.Callable"
else: "nits no bloquean: si la PR crece >300 líneas de diff, dividir (nits-storage /
       nits-scripts)"
preconditions: [Waves 0-3 merged]
expected_result: "memoria #11902 sin nits abiertos; review siguiente más barato"
failure_condition: "un nit introduce cambio de comportamiento no cubierto por tests"
failure_action: "revertir ese nit individual y re-agendar con test propio"
touches: [fts.py, structured.py, sqlite_backend.py, scripts/]
validation: "suite completa + greps específicos por nit (0 prints ya en T-08; 0 vars muertas; etc.)"
```


---

## PHASES 5-9 — PROTOCOLO DE EJECUCIÓN (runtime por tarea)

Cada tarea T-0X se ejecuta con este loop (no es decoración: es el contrato de
ejecución del plan — toda transición responde IF/THEN/ELSE).

### 5 · CUE DETECTION (inicio de tarea)
```text
IF precondition de T-0X no satisfecha
THEN acquire_information (tarea previa / PO) → vuelve a CUE
ELSE IF invalidation_condition del commitment activa
THEN invalidate_commitment → DELIBERATION (re-plan)
ELSE implementation_intention_matches → ACTION_SELECTION
```

### 6 · ACTION SELECTION
```text
Prioridad = GOAL_PROGRESS + INFORMATION_GAIN + EXPECTED_BENEFIT
          − (COST + RISK + IRREVERSIBILITY)
IF preconditions ok AND riesgo aceptable AND acción reversible (branch git + backup)
THEN EXECUTION
ELSE IF falta dato obtainable → INFORMATION_ACQUISITION
ELSE → DELIBERATION
```
Regla específica: toda tarea abre en rama `review-fix/<task-id>` desde
`adr-065-f1-f6`; la reversibilidad es condición de entrada, no deseo.

### 7 · EXECUTION
Sin re-deliberar salvo invalidación crítica. Prohibido en execution:
fallbacks implícitos, prints en shared_kernel, skips de tests, ediciones fuera de
`touches`.

### 8 · FEEDBACK (clasificación obligatoria)
```text
MATCH            → update_world_state → VALIDATION
PARTIAL_MATCH    → CUE DETECTION (siguiente intención, misma tarea)
FAILURE          → RECOVERY (phase 10)
UNEXPECTED_EFFECT→ update_beliefs → DELIBERATION (¿invalida el plan?)
```

### 9 · VALIDATION (por tarea — criterio objetivo, no impresión)
```text
IF validation de T-0X pasa AND suite completa verde (≥497, sin skips nuevos)
   AND canónica v3 intacta cuando la tarea tocó storage (verify_canonical_v3)
THEN commit en rama de tarea → TERMINATE(tarea) → CUE(siguiente tarea)
ELSE IF acción adicional produce progreso → CUE DETECTION
ELSE → RECOVERY
```

### 10 · RECOVERY
```text
LOOP DETECTION: mismo estado + misma acción + mismo resultado → NO REPETIR.
IF alternativa existe (rama ELSE de la intención)
THEN ACTION_SELECTION con la alternativa
ELSE IF el fallo invalida una asunción del commitment
THEN DELIBERATION (re-plan local, update del plan doc)
ELSE TERMINATION con causa documentada (nunca silenciosa)
```

### 13 · TERMINATION (global del plan)
```text
IF los 5 success_criteria del commitment en verde
THEN cerrar: merge secuencial Waves 0→4, actualizar Anexo A del ADR-075
     (deuda H0-librería = cerrada, M-5 = cerrada), memoria de cierre
ELSE IF pendiente solo decisión PO (T-07a, schema trace)
THEN TERMINATION parcial: merge Waves 0-2 + T-08..T-11, dejar T-07a/T-12 según T-11 documentados
ELSE RECOVERY
```


---

## CUMPLIMIENTO AGENTS.md (obligatorio para aprobación del plan)

### failure_scenarios (≥3, con fallo/causa/mitigación/detección)

```json
{
  "failure_scenarios": [
    {
      "id": "FS-P1",
      "fallo": "El fix de T-05 (dirty set de edges) diverge del estado real: un edge modificado sin marcar dirty se persiste con contenido viejo",
      "causa_raiz": "cache de raw serializado desalineado tras un path de escritura no cubierto",
      "mitigacion": "todo mutador de edges pasa por la misma marca dirty; test de paridad roundtrip en fixture 16K",
      "deteccion": "BDD roundtrip de T-05 + verify_canonical_v3 (conteo edges) tras Wave 2"
    },
    {
      "id": "FS-P2",
      "fallo": "El chequeo barato de T-06 da falso negativo: FTS corrupto con conteos iguales no se reconcilia en boot",
      "causa_raiz": "el conteo es primera señal, no prueba de integridad",
      "mitigacion": "branch ELSE declarada: escalar a sample hash; el drift estructural queda cubierto por fsync de FS-2 y por verify script",
      "deteccion": "test de corrupción FTS de T-06 (término inyectado + conteo intacto)"
    },
    {
      "id": "FS-P3",
      "fallo": "T-12a (CAS) introduce falso positivo en proceso único legítimo (save rechazado sin writer externo)",
      "causa_raiz": "contador de generación actualizado fuera de la transacción",
      "mitigacion": "leer y verificar generación dentro de la MISMA transacción sqlite; BDD mismo-proceso exige cero falso positivo",
      "deteccion": "test multi-conexión de T-12a (rama misma-proceso)"
    },
    {
      "id": "FS-P4",
      "fallo": "T-10 (SSOT de schemas) rompe un consumidor que importaba la copia literal con un field extra no canónico",
      "causa_raiz": "drift preexistente entre las 3 copias que nadie comparó",
      "mitigacion": "antes de refactor: diff automatizado de las 3 copias; cualquier diferencia se resuelve ANTES (o se registra como hallazgo nuevo)",
      "deteccion": "test de completitud del SSOT + suite completa (los schemas alimentan composition + migraciones)"
    }
  ]
}
```

### Métricas por fase (formato checklist M-0X, ≥3 por fase)

```text
Wave 0 (hotfix herramientas):
- [ ] M-01 Todo check de verificación termina en assert o exit explícito — anti-patrón: imprimir FAIL y exit 0
- [ ] M-02 El backup de remediación precede a toda mutación — anti-patrón: restore-point que no contiene el pre-estado
- [ ] M-03 Validación DDL por igualdad de sets (completitud) — anti-patrón: assert de membresía que acepta exceso

Wave 1 (gate H0 librería):
- [ ] M-04 Cada check retorna Result[Report, StorageStateError] — anti-patrón: excepción cruda cruzando la frontera de la librería
- [ ] M-05 Tests BDD cubren caso ok + fallo forrado por check (H-0..H-6) — anti-patrón: solo happy path
- [ ] M-06 Mensajes de error incluyen remediación accionable — anti-patrón: "storage inválido" sin qué hacer

Wave 2 (performance pre-rebuild):
- [ ] M-07 add_edge individual no paga O(E) en serialización — anti-patrón: re-dumps globales por operación
- [ ] M-08 Boot en estado sano no ejecuta full reconcile — anti-patrón: scan O(docs+terms) por apertura
- [ ] M-09 Todo fallback eager emite warning + métrica — anti-patrón: degradación silenciosa de coste

Wave 3 (plataforma):
- [ ] M-10 Cero prints en shared_kernel (meta-test) — anti-patrón: log a stdout que corrompe JSON-RPC
- [ ] M-11 metrics() reporta solo valores medidos (pragma reales) — anti-patrón: constantes hardcodeadas
- [ ] M-12 FS-8/M-03/FS-5 automatizados o declarados no-aplica — anti-patrón: métrica omitida en silencio

Wave 4 (H-1 + nits):
- [ ] M-13 La decisión multi/mono-proceso está citada del contrato, no inferida — anti-patrón: guessing (meta-rule)
- [ ] M-14 Escenario B-stale reproducible queda cubierto por test — anti-patrón: fix teórico sin BDD
- [ ] M-15 La PR de nits no cambia comportamiento sin test — anti-patrón: refactor que arrastra semántica
```

### Checklist de segundo orden (respuestas)

1. **¿Qué módulos importan esto?** `composition.py` (schemas), scripts de migración,
   tests BDD de storage — todos listados en `touches` por tarea.
2. **¿Side effects?** T-01/T-12 tocan escritura a BD: ambos con backup/branch previa.
   T-05/T-06 cambian write/boot path: cubiertos por paridad + canónica v3 check.
3. **¿Eventos de dominio?** Ninguno: el plan vive en la capa storage/shared_kernel;
   no publica ni altera eventos.
4. **¿Tests dependen?** `test_fts5_storage_bdd.py`, `test_structured_sql_bdd.py`,
   `test_lazy_readmodel_bdd.py` (45 tests) + suite 497 como regresión global.
5. **¿Qué cambia en MCP adapter, CLI, CKG, DeltaGraph, Trace?** MCP adapter: nada
   (stdout protegido por T-08). CLI: gate H0 invocable (T-04). CKG/DeltaGraph/Trace:
   sin cambio semántico; T-10 unifica sus schemas; el quirk trace queda tal cual
   hasta decisión PO.


---

## ÍNDICE DE TAREAS Y DEPENDENCIAS

| Tarea | Wave | Hallazgo | Pri | Est | Precondición | Verificación |
|---|---|---|---|---|---|---|
| T-01 backup pre-mutación | 0 | H-3, L9 | HIGH | S | restore-point | BDD backup/restore |
| T-02 verify por igualdad | 0 | M-1, L10 | MED | S | BD v3 real | mutación → assert falla |
| T-03 fail-loud verify | 0 | M-7 | MED | S | T-02 | drift → exit≠0 |
| T-04 gate H0 librería | 1 | deuda Anexo A | HIGH | M | T-01..03 | tests/test_pre_migration_bdd.py |
| T-11 contrato multi-proceso | ∥ | H-1 (previo) | INFO | S | — | decisión en ADR |
| T-05 edges O(touched) | 2 | M-2 | HIGH | M | paridad edges | spy dumps ≤ touched |
| T-06 boot reconcile barato | 2 | M-3 | MED | S | — | corrupción detectada |
| T-07 fallback visible | 2 | M-4 | MED | M | PO o default (b) | warning+conteo docs |
| T-08 logging | 3 | H-2 | HIGH | S | — | grep prints = 0 |
| T-09 metrics+benchmarks | 3 | M-5 | HIGH | M | fixture 16K | benchmark en suite |
| T-10 SSOT schemas | 3 | M-6 | HIGH | S | — | completitud + grep meta-test |
| T-12a/12b H-1 | 4 | H-1 | HIGH/MED | M/S | T-11 | BDD multi-conexión |
| T-13 nits batch | 4 | L1-L11 | LOW | S | Waves 0-3 | greps + suite |

**Camino crítico:** T-01 → T-04 (deuda Anexo A) y T-11 → T-12a/b (única bifurcación
por decisión externa). Todo lo demás es paralelizable dentro de su wave.

**Criterios de cierre global** (= success_criteria del Rubicon):
1. Suite ≥497 verde, sin skips nuevos, tras cada tarea.
2. Canónica v3 intacta tras tareas de storage (verify_canonical_v3, exit 0 — T-03).
3. Deuda Anexo A #1 cerrada (T-04 con tests BDD + StorageStateError).
4. Deuda M-5 cerrada (T-09: FS-8/M-03/FS-5 automatizados o declarados).
5. SSOT de schemas con test de completitud (T-10) — decisión PO a 1 línea.

**Fuera de alcance de este plan** (documentado, no omitido):
- Decisión PO del schema trace (quirk event_type) → habilitada por T-10; requiere
  drop/re-backfill de trace; suya la ventana, 1 línea + migración.
- Rebuild cpg/ckg (H-6) → desbloqueado por Wave 2; sigue siendo tarea del ADR-075.
- L4/L5/L6 (optimizaciones de búsqueda y cache de get) → re-evaluar post-rebuild
  cuando existan datos reales de uso; hoy son especulación.

---

## META-RULE DE CONTROL (aplicable antes de cada merge)

1. ¿Qué cambió? → delta de la tarea (touches + tests nuevos)
2. ¿Objetivo activo? → la tarea en curso, dentro del commitment
3. ¿Fase? → la del protocolo 5-9 (declarada en cada PR)
4. ¿Condiciones IF activas? → las de la implementation_intention
5. ¿Acción THEN? → la definida; si no aplica, la ELSE
6. ¿Qué pasa ELSE? → definido por tarea (nunca "improvisar")
7. ¿Qué invalidaría esta interpretación? → invalidation_conditions del Rubicon

Si alguna respuesta no puede darse con evidencia: NO GUESS → INFORMATION_ACQUISITION
o DELIBERATION, y actualizar este documento antes de continuar.











