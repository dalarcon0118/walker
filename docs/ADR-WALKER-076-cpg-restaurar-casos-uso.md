# ADR-WALKER-076 — Restaurar el CPG como memoria consultable del código

> **Status:** Propuesta (pendiente aprobación PO)
> **Fecha:** 2026-09-05
> **Metodología:** rule.md (ACTION-CENTRIC AGENT WORKFLOW) — fases 0-4 construyen
> este documento; fases 5-9 son el protocolo de ejecución de sus tareas.
> **Origen:** auditoría de integración de `code_analysis` con las tools y con
> CognitiveGraph (memorias #11921, #11923) y la taxonomía de memorias de project.md.
> **Relacionados:** ADR-WALKER-075 (storage v3), ADR-054 D4 (data_dir canónico),
> ADR-062 (resolución id+contrato), ADR-068 (schemas SSOT).

---

## PHASE 0 — OBSERVE (hallazgos verificados, no interpretados)

**Facts** (cada uno con evidencia archivo:línea, verificado esta sesión):

| # | Hallazgo | Tipo | Evidencia |
|---|---|---|---|
| R-A | Path-guessing en lecturas del CPG: 3 instancias del anti-patrón `os.path.join(root,"data")` (regla ADR-054 D4 violada en el read path) | BUG | `query_service.py:41-47` (2 call-sites: `code_context_service.py:38`, `code_analysis_service.py:39`), `bug_service.py:361,378` (`_scan_cfg_on_demand_core`/`_scan_dfg_on_demand_core`) |
| R-S | Tool `code_search` buscaba en el **CKG** (CkgReadService) en vez del CPG | BUG de wiring | `memories/handlers/code_analysis.py` (corregido en sesión) |
| HU-1 | `check_cpg_health.py`: use case completo sin ningún caller ni tool | Huérfano | `application/use_cases/check_cpg_health.py` |
| HU-2 | `check_conventions` (conventions.py): **puente CKG↔CPG** — valida código real contra convenciones del CKG — sin tool; inyección `ckg` ya prevista (§3bis R1) | Huérfano ⭐ | `application/use_cases/conventions.py` |
| HU-3 | `hallucination.py`: detector de imports alucinados (~500 líneas, migrado del legacy) sin ningún consumidor en el repo | Huérfano | `infrastructure/hallucination.py` |
| HU-4 | `sg_adapter.py` y `codereview/main.py`: legacy sin importadores (la vía viva es `review_code`→`deep.py`) | Muerto probable | `infrastructure/` |
| P-1 | **DFG sin caso de uso de consulta**: la proyección se construye (`build_service.py:1206` → `dfg.json`) y se consulta solo internamente en taint paths de `find_bugs`; no existe "dame el data-flow de X" | Perdido (legacy) | `bug_service.py`, `cpg_dfg_scan.py` borrado en `cf9fd79` |
| P-2 | **CFG sin caso de uso de consulta**: `_scan_cfg_on_demand` (reachability) solo para bugs/security; no existe "muéstrame el control-flow de f" | Perdido (legacy) | `bug_service.py:361` |
| P-3 | Ramas del DSL sin tool: `_handle_trace` y `_handle_relations` de `QueryGraphUseCase` no son disparables | Parcialmente perdido | `query_service.py` |
| AR-1 | Inversión de capas: `domain/builder.py:256` importa `infrastructure/adapters/walker_cpg_adapter` (dominio→infra) | Deuda arquitectónica | viola domain-first (AGENTS.md) |
| AR-2 | `trace_event_publisher` suscripto al EventBus del CPG es stub que solo loggea: los eventos del CPG no llegan a TraceGraph | Integración rota | `builder.py:253`, memoria #11921 R-B |
| AR-3 | Instancias de repo duplicadas en `QueryGraphUseCase` y `CpgService.grep/read` | Deuda | #11921 R-C |
| T-C0 | Correcciones ya aplicadas SIN commit esta sesión: R-A (3 sitios), R-S (code_search→CPG), actions bugs/security fail-loud | Pendiente de commit | `git status` 17 archivos |

**Inferencias (no facts):** HU-4 es borrable si ningún plan lo revive (decisión PO).
**Suposiciones:** el rebuild H-6 llenará `cpg` (~16K docs) y todos los use cases
deben leer la canónica ANTES de eso.
**Contradicciones:** ninguna con ADR-075; P-1/P-2 coexisten con el contrato
`materialize_all()` (`sqlite_backend.py:272`, `ports.py:117`) que ya documenta a
"scan_cfg, DFG, quality" como consumidores previstos — la infraestructura espera
estos casos de uso; nunca se escribieron.

---

## PHASE 1 — STATE CONSTRUCTION

- **Dónde:** rama `adr-065-f1-f6` (HEAD `b9bae7d`); ADR-075 ejecutado y remediado
  (T-01..T-13); storage v3 estable; suite 569 verde (los 3 rojos de
  `test_trace_messages_ckg_bdd.py` documentados en Phase 1 quedaron verdes en
  esta sesión — fix T-14 load_traces/trace cache + T-15 supersedes persistido).
- **Problema:** el CPG —memoria del código según project.md— no cumple su propósito:
  lecturas con path-guessing (R-A), una tool mal cableada (R-S), 4+ use cases
  huérfanos (HU-1..HU-4) y DFG/CFG/code-review del legacy sin caso de uso de consulta
  (P-1..P-3).
- **Qué cambió:** esta sesión aplicó sin commit R-A (3 sitios), R-S y fail-loud en
  bugs/security (T-C0).
- **Objetivo activo:** (candidato, Phase 2) consolidar correcciones, habilitar
  huérfanos y reconstruir casos de uso perdidos en `code_analysis` y `tasks`.
- **Restricciones:** AGENTS.md — failure_scenarios (≥3), métricas por fase (≥3),
  fail-fast sin fallbacks (el guessing R-A es su violación directa), `Result[T,E]`,
  domain-first (AR-1 deuda contra esta regla), data_dir canónico resuelto por el
  caller (ADR-054 D4), schemas desde SSOT (ADR-068/T-10), patrón de completitud para
  registries. SSOT delta/trace NUNCA se tocan.
- **Recursos:** proyecciones ya construidas por el build (`dfg.json`,
  `quality_report.json` en `CPG_DERIVED_KEYS`); `materialize_all()` del backend
  diseñado para estos consumidores; dominio `Graph`/`Query` con semántica
  taint/reachability; handler `memories/handlers/code_analysis.py` como punto de
  exposición; `features/tasks/handlers/` con `register_all_tasks` (patrón de
  completitud aplicable).
- **Información faltante:** ninguna bloqueante — inventario de call-sites y
  consumidores completo (grep exhaustivo, memorias #11921/#11923).
- **Incertidumbres:** (a) plan para HU-4 (`sg_adapter`, `codereview/main`) → decisión
  PO, no bloquea; (b) exposición de métricas de tamaño CFG/DFG → se expone
  estructura, no benchmarks.

---

## PHASE 2 — DELIBERATION

**GOAL:** consolidar el CPG como memoria consultable: (1) commit de T-C0, (2) exponer
huérfanos como acciones/tools con patrón de completitud, (3) reconstruir casos de uso
perdidos del legacy (dataflow/controlflow sobre proyecciones, trace/relations del
DSL, hallucination en verify, health como diagnóstico) en `code_analysis` y `tasks`,
(4) registrar deuda AR-1..AR-3 con corrección planificada.

**DESIDERABILITY:** valor = el propósito declarado del CPG pasa de "infraestructura
lista" a "cumplido"; el circuito CKG↔CPG (HU-2) activa la integración entre memorias;
la anti-alucinación pagada (HU-3) entra en uso; se elimina la última familia de
path-guessing. Costo ≈ 9 tareas acotadas (wiring + thin actions); riesgo bajo:
proyecciones y dominio ya existen. Segundo orden: más superficie → contratos de
superficie (29→N) y tests de completitud a actualizar.

**FEASIBILITY:** todos los fixes tienen patrón que copiar (providers.py:101 para
data_dir; `inspect_codebase action=*` para exposición; `verify_predicates` para
hallucination; `register_all_tasks` para tasks). Evidencia suficiente: inventario
verificado por grep de cada caller.

**ADVERSARIAL VALIDATION:**
- *¿Qué invalidaría la estrategia?* Si el rebuild H-6 generara DFG con formato
  distinto al que consumen bug_service y las actions nuevas — mitigación: consumir
  `_load_projection("dfg")` (la MISMA fuente de find_bugs); un cambio de formato
  toca una sola función.
- *Hipótesis alternativa:* tools MCP nuevas por huérfano. Descartada por costo:
  rompe el contrato de superficie (29→N+4) y duplica catálogos; las acciones reusan
  dispatch/validación/sesión probados. Excepción barata DESPUÉS: promover conventions
  a tool propia si el PO la quiere de primer nivel.
- *Suposición no validada:* que nada depende de que `code_search` busque en el CKG —
  verificado: los usos reales (trace code_search×2) pedían conocimiento de código que
  el CKG vacío no podía dar; el fix corrige exactamente eso.
- *Evidencia contradictoria:* ninguna — `materialize_all()` documenta a estos
  consumidores como previstos.

**RULE:** goal deseable ∧ factible ∧ evidencia suficiente → **select_goal → RUBICON**.

---

## PHASE 3 — RUBICON / COMMITMENT

```yaml
commitment:
  status: committed
  goal: >
    Ejecutar W1..W9 (4 waves) para que la memoria CPG cumpla su propósito
    declarado, con las correcciones T-C0 committeadas como primer hito.
  success_criteria:
    - "T-C0 committeado; suite 569 verde sin regresiones nuevas"
    - "code_context y business_layer leen la canónica (test conductual: repo._data_dir == paths.data_dir)"
    - "inspect_codebase expone actions dataflow|controlflow (file+symbol) sobre proyecciones existentes"
    - "check_conventions alcanzable como acción: circuito CKG↔CPG operativo (con CKG poblado) o error explícito de índice vacío (fail-fast, sin fallback)"
    - "hallucination integrado en verify_predicates (action=imports)"
    - "_handle_trace y _handle_relations del DSL disparables (inspect_codebase action=query)"
    - "check_cpg_health expuesto como diagnóstico; sg_adapter y codereview/main con decisión PO registrada (borrar o plan)"
    - "AR-1 corregido (dominio no importa infra); AR-2/AR-3 como issues documentados con owner"
    - "Test de completitud: EXPECTED_ACTIONS == set(actions de inspect_codebase) — patrón AGENTS.md"
  assumptions:
    - "Las proyecciones dfg.json/quality_report.json del build son la fuente de las actions de consulta"
    - "No hay consumidores externos del comportamiento previo de code_search (buscaba en CKG vacío)"
  invalidation_conditions:
    - "El formato de dfg.json que produzca el rebuild H-6 difiere del que consume find_bugs hoy → re-deliberar la fuente"
    - "Exponer conventions requiere lógica de negocio nueva (no wiring) → re-estimar costo"
```

**RULE:** commitment_conditions_met → **transition_to(IMPLEMENTATION_DESIGN)**.

---

## PHASE 4 — IMPLEMENTATION DESIGN (tareas = implementation_intentions)

### Wave 1 — Consolidar lo corregido (T-C0) y cerrar la familia de path-guessing

> **ESTADO (2026-09-06): W1 ✅ CERRADO** — commit `82d9b30` (22 files, 897+/55-):
> T-C0 completo (code_search→CPG R-S, bugs/security fail-loud, data_dir explícito
> en bug_service/code_analysis_service/code_context_service) + T-14/T-15
> (trace_message tool 29 en superficie; load_traces sin guard de chain_dir, cache
> key por id(cg); supersedes persistido). Los 3 rojos documentados
> (test_trace_messages_ckg_bdd.py) quedaron **verdes** en este commit — la suite
> dejó de tener rojos en esta rama: **569 passed al cierre de W1**.

```yaml
id: W1-commit-tc0                    # prioridad: HIGH · est: S — ✅ DONE 82d9b30
if:   {condition: "los fixes R-A (3 sitios), R-S (code_search→CPG) y fail-loud de bugs/security están aplicados sin commit"}
then:
  action: "commit atómico de T-C0 (código+tests de contrato de superficie ya ajustados)
    con mensaje que referencie este ADR y las memorias #11921/#11923"
else:
  action: "si algún fix muestra regresión al re-ejecutar la suite, aislar el archivo
    culpable y corregir ANTES de commitear (no commitear rojo)"
preconditions: [suite 564 verde + 3 rojos documentados sin cambio]
expected_result: "working tree limpio para código; los 3 tests de superficie con 29 tools"
failure_condition: "suite con regresiones nuevas respecto al baseline"
failure_action: "git checkout del archivo culpable, re-aplicar fix, re-ejecutar"
next_state: W2
touches: [git; sin código nuevo]
```

> **ESTADO (2026-09-06): W2 ✅ CERRADO** — commit `7d3cb5e` (5 files, +140/-27):
> QueryGraphUseCase/ReviewService con fail-fast (ValueError "data_dir no
> inyectado"); schema._register_ref_indexes usa `graph.chain_dir` directo
> (v3: los refs viven en data_dir/cpg.ref.*, no en <chain_dir>/data — legacy
> inexistente en v3 → refs quedaban sin registrar en silencio); CPGBuilder sin
> fallback legacy y stats() con self._inner.data_dir. Test NUEVO
> test_fast_mcp_cpg_query_failfast_bdd.py (5 tests, incluye M-06: 0
> ocurrencias de os.path.join(project_root, "data")). **574 passed al cierre.**

```yaml
id: W2-failfast-data-dir             # prioridad: HIGH (R-A raíz) · est: S — ✅ DONE 7d3cb5e
if:   {condition: "QueryGraphUseCase.repo aún contiene el guessing resolve_project_root+os.path.join(root,'data')"}
then:
  action: "reemplazar el guessing por fail-fast: si data_dir no fue inyectado →
    ValueError con remediación ('pase data_dir=ProjectPaths.data_dir resuelto por
    ProjectService.resolve_paths'); actualizar los 2 call-sites restantes al patrón
    providers.py:101 si quedara alguno; añadir test conductual
    (repo._data_dir == paths.data_dir para code_context y business_layer)"
else:
  action: "si apareciera un 4º call-site con guessing, aplicarle el mismo patrón y
    sumarlo al test de completitud de callers"
preconditions: [W1, inventario de call-sites vigente (memoria #11921)]
expected_result: "cero ocurrencias de os.path.join(root,'data') en code_analysis; los tests de layout tmp_path que dependían del fallback migrados a inyección explícita"
failure_condition: "algún test de layout falla por falta del fallback"
failure_action: "migrar ese fixture a inyección (no restaurar el fallback)"
next_state: W3
touches: [features/code_analysis/application/query_service.py, code_context_service.py, code_analysis_service.py, tests/]
```

---

### Wave 2 — Casos de uso perdidos del legacy (DFG/CFG consultables, DSL, tasks)

```yaml
id: W3-actions-dataflow-controlflow  # prioridad: HIGH (P-1, P-2) · est: M
if:   {condition: "no existe forma de consultar data-flow de un símbolo ni control-flow (solo taint interno de find_bugs)"}
then:
  action: "añadir a inspect_codebase las actions dataflow y controlflow (params file+symbol):
    dataflow → _load_projection('dfg') (misma fuente de find_bugs) + Graph/Query del dominio
    para paths de datos del símbolo; controlflow → _scan_cfg_on_demand (reachability) del
    bug_service refactorizado a helper compartido; salida compacta (nodos/edges del
    subgrafo + resumen), fail-fast si la proyección no existe ('ejecute build_code_index')"
else:
  action: "si el formato de dfg.json difiere de lo esperado, NO adivinar: fallar con
    el shape encontrado vs esperado (misma lección M-1 de completitud)"
preconditions: [W2 (rutas canónicas garantizadas), build_code_index ya corrió para el proyecto]
expected_result: "inspect_codebase action=dataflow file=X symbol=Y retorna el flujo de datos real desde la proyección canónica"
failure_condition: "action retorna vacío sin error cuando no hay proyección"
failure_action: "convertir a error explícito con remediación (fail-fast)"
next_state: W4
touches: [services/code_analysis_service.py, bug_service.py (extraer helper), memories/handlers/code_analysis.py, phase2_memories.py (schema/desc)]
```

```yaml
id: W4-dsl-trace-relations           # prioridad: MEDIUM (P-3) · est: S
if:   {condition: "_handle_trace y _handle_relations del DSL de QueryGraphUseCase no son disparables por ninguna tool"}
then:
  action: "exponer inspect_codebase action=query con param query (DSL existente):
    delega en QueryGraphUseCase con data_dir canónico (W2); documentar la sintaxis
    aceptada en la descripción de la acción (trace(...), relations(...))"
else:
  action: "si el DSL resulta tener ramas no documentadas, documentar solo las operativas y registrar las otras en issues"
preconditions: [W2]
expected_result: "query 'trace(f)' y 'relations(g)' retornan resultados desde la canónica"
failure_condition: "la action filtra silenciosamente ramas del DSL"
failure_action: "error explícito 'rama de DSL no soportada: X'"
next_state: W5
touches: [services/code_analysis_service.py, memories/handlers/code_analysis.py, phase2_memories.py]
```

```yaml
id: W5-tasks-cpg-integration         # prioridad: MEDIUM · est: M
if:   {condition: "los handlers de tasks (implement, verify, capture) no consumen el CPG canónico ni sus nuevas capacidades"}
then:
  action: "integrar vía WalkerCpgAdapter/get_analysis_adapter (puerto, no reconstrucción):
    implement → gather_cpg_context para el objetivo de la tarea; verify → action imports
    de hallucination (W6) como predicado adicional; completitud: EXPECTED_TASKS ==
    set(register_all_tasks) ampliado con los consumos declarados"
else:
  action: "si un handler ya consume adapter hoy, registrar el consumo en el test de completitud en vez de duplicar la llamada"
preconditions: [W2; patrón §3bis R1 (inyección desde composition root)]
expected_result: "tasks leen el CPG vía adapter con data_dir canónico; cero imports features→shared al revés"
failure_condition: "un handler construye su propio repo (servicio locador)"
failure_action: "re-factor al patrón de inyección (composition root decide)"
next_state: W6
touches: [features/tasks/handlers/implement.py, verify.py, capture.py, composition]
```

---

### Wave 3 — Huérfanos existentes (habilitar, no reescribir)

```yaml
id: W6-hallucination-verify          # prioridad: HIGH (HU-3, código ya pagado) · est: S
if:   {condition: "infrastructure/hallucination.py no tiene ningún consumidor"}
then:
  action: "añadir action=imports a verify_predicates: para los files de la tarea,
    detectar imports alucinados (no resueltos en el proyecto) y fallar el predicado
    con el listado; inyección del detector desde composition (§3bis R1)"
else:
  action: "si el detector requiere indexado previo (CPG vacío), error explícito
    'ejecute build_code_index' — nunca pasar por alto con 0 falsos alucinados"
preconditions: [W2; CPG construido para el proyecto]
expected_result: "verify_predicates action=imports detecta un import inexistente plantado en fixture y falla con el listado"
failure_condition: "el predicado pasa con CPG vacío"
failure_action: "hacer fail-fast sobre índice vacío (no '0 problemas')"
next_state: W7
touches: [features/tasks/handlers/verify.py, composition, memories/handlers]
```

```yaml
id: W7-conventions-ckg-cpg           # prioridad: HIGH (HU-2, puente entre memorias) · est: S
if:   {condition: "check_conventions (conventions.py) sigue sin caller: el circuito CKG↔CPG existe solo en el schema"}
then:
  action: "exponer como action=conventions de inspect_codebase: valida los files
    objetivo contra las convenciones activas del CKG (query_active type=convention);
    inyectar ckg_service desde composition (ya previsto por §3bis R1); si el CKG
    está vacío → error explícito con remediación ('ejecute extract_knowledge'),
    nunca [] silencioso"
else:
  action: "si el PO quiere tool de primer nivel, promover después (la acción interna es el paso 1 barato)"
preconditions: [W2; CKG con datos tras extract_knowledge (H-6)]
expected_result: "action=conventions retorna violaciones reales con la convención CKG como evidencia"
failure_condition: "con CKG vacío retorna vacío sin error"
failure_action: "fail-fast con remediación"
next_state: W8
touches: [services/code_analysis_service.py, memories/handlers/code_analysis.py, composition (inyección ckg), phase2_memories.py]
```

```yaml
id: W8-health-and-legacy-decision    # prioridad: LOW (HU-1, HU-4) · est: S
if:   {condition: "check_cpg_health sin caller; sg_adapter.py y codereview/main.py sin importadores"}
then:
  action: "check_cpg_health → action=health de inspect_codebase (diagnóstico: node
    counts, proyecciones presentes, edad del índice); sg_adapter y codereview/main →
    decisión PO explícita registrada en este ADR: borrar (por defecto, sin plan que
    los reviva) o mantener con owner"
else:
  action: "si la PO reviva alguno, moverlo a application/ con puerto propio — no dejarlo en infrastructure/ como residuo"
preconditions: [W2]
expected_result: "health consultable; cero archivos legacy sin dueño en infrastructure/"
failure_condition: "archivos muertos permanecen sin decisión"
failure_action: "issue con owner y fecha de decisión"
next_state: W9
touches: [services/, infrastructure/ (borrado o traslado), docs de decisión]
```

```yaml
id: W9-arch-debt                     # prioridad: MEDIUM (AR-1) · est: M
if:   {condition: "domain/builder.py:256 importa infrastructure/adapters/walker_cpg_adapter (inversión de capas)"}
then:
  action: "invertir: extraer el puerto que builder necesita a domain/ports.py y que
    el composition root lo inyecte (el adapter implementa el puerto); AR-2
    (trace_event_publisher stub) y AR-3 (repos duplicados) → issues con owner,
    severidad y-dependencia del rebuild H-6"
else:
  action: "si la inversión toca el build path en caliente, hacerla tras H-6 y registrar el orden en el issue"
preconditions: [W1-W8 mergeados; suite verde]
expected_result: "features/code_analysis/domain/ sin imports de infrastructure/ (verificado por test de imports, patrón test_shared_kernel_no_prints)"
failure_condition: "el dominio vuelve a importar infra en algún hotfix futuro"
failure_action: "el test de imports falla en CI (red de seguridad permanente)"
next_state: VALIDATION (criterios del Rubicon)
touches: [domain/builder.py, domain/ports.py, composition, tests]
```

<!-- APPEND -->

---

## PHASE 5 — failure_scenarios (AGENTS.md: ≥3, con fallo/causa/mitigación/detección)

| # | Fallo | Causa raíz | Mitigación | Detección |
|---|---|---|---|---|
| FS-1 | Vuelve el path-guessing: una lectura del CPG adivina `os.path.join(root,"data")` → la BD parásita en la raíz del repo se sirve como canónica (R-A reaparece) | un call-site nuevo copia el anti-patrón viejo; el home del guessing (`QueryGraphUseCase.repo`) no tiene fallback fail-fast | W2: fail-fast si `data_dir` no fue inyectado (`ValueError` con remediación) + test conductual `repo._data_dir == paths.data_dir` para code_context y business_layer; patrón provable providers.py:101 | test de completitud de callers: grep 0 de `os.path.join(root,"data")` en code_analysis (CI) |
| FS-2 | `action=dataflow` devuelve vacío cuando no existe `dfg.json` → el agente cree que el símbolo no tiene data-flow (falso negativo) | proyección derivable ausente (rebuild H-6 no corrido) y acción sin guard fail-fast | W3: fail-fast con remediación (`ejecute build_code_index`) si la proyección no existe; la action nunca retorna `[]` por proyección faltante | el propio guard (error explícito ≠ vacío); test de la action sin proyección |
| FS-3 | `action=conventions` (HU-2) retorna `[]` con CKG vacío → "cero violaciones" silencioso; el circuito CKG↔CPG parece operar cuando no hay datos | el CKG está en 0 docs hasta el capture (H-6) y la acción no distingue "sin convenciones" de "sin datos" | W7: fail-fast con remediación (`ejecute extract_knowledge`) si `query_active(type=convention)` trae 0; nunca `[]` silencioso | test de la action con CKG vacío |
| FS-4 | `verify_predicates action=imports` (HU-3) pasa con CPG vacío → 0 falsos alucinados y la anti-alucinación queda desactivada en silencio | el detector depende del índice; índice vacío → no hay imports que resolver | W6: fail-fast sobre índice vacío (`ejecute build_code_index`); el detector inyectado desde composition (§3bis R1), nunca construido en el handler | test de la action con CPG vacío |
| FS-5 | AR-1 reaparece: `domain/` vuelve a importar `infrastructure/` (inversión de capas) en un hotfix | sin red de seguridad, la deuda vuelve; el import dormido no se detecta en review | W9: extraer el puerto a `domain/ports.py` + test de imports (patrón `test_shared_kernel_no_prints`) que falla si `features/code_analysis/domain/` importa infra | el test de imports corre en CI (red permanente) |
| FS-6 | `code_search` cambia de CKG a CPG (R-S) y un consumidor externo esperaba conocimiento (CKG) → sondeo contra índice vacío devuelve resultados de código sin aviso | fix de wiring sin auditar consumidores | verificado: los usos reales (trace code_search×2) pedían conocimiento de código; el CKG vacío no podía dar evidencia — el fix corrige exactamente eso; contrato documentado en la descripción de la tool | test de superficie: descripción de `code_search` menciona `source: cpg` (contrato visible) |
| FS-7 | Heurística anterior `is_successful` → dict: un resultado con `result` no-dict se degrada a vacío estructura sin contarlo | la action regresa a "return best-effort" en vez de fail-loud | T-C0 hace fail-loud los fallos de bugs/security; W3/W4 exponen solo salidas existentes o error explícito | tests de las actions con fixture rota |

---

## PHASE 6 — Métricas por fase (obligatorio, AGENTS.md; tabla M-01..M-06)

Cada wave declara ≥3 métricas aplicables sobre las áreas que toca. Las no listadas por una wave se declaran no aplicables (no se omiten en silencio).

### Wave 1 — Consolidar T-C0 y cerrar family de path-guessing (W1, W2)

- [ ] M-06 (configuración): 0 ocurrencias de `os.path.join(project_root, "data")` en `features/code_analysis/` (grep en CI) — anti-patrón a evitar: path-guessing / rutas adivinadas (R-A)
- [ ] M-03 (dominio): `QueryGraphUseCase.repo` recibe `data_dir` inyectado o falla con `ValueError` accionable — anti-patrón a evitar: fallback silencioso a `<repo_root>/data`
- [ ] M-01 (escritura): commit T-C0 atómico con suite 569 verde; los 3 tests de superficie con 29 tools — anti-patrón a evitar: commitear rojo / mezclar streams

### Wave 2 — Casos de uso perdidos (W3, W4, W5)

- [ ] M-01 (escritura): `action=dataflow|controlflow` retornan desde `_load_projection('dfg')`/helper compartido — anti-patrón a evitar: duplicar la lectura de proyecciones (divergencia con find_bugs)
- [ ] M-03 (dominio): sin proyección → error explícito con remediación (nunca `[]` ni `{}`) — anti-patrón a evitar: falso negativo silencioso
- [ ] M-05 (concurrencia): los handlers de tasks consumen el CPG vía adapter inyectado (cero repos construidos en el handler = servicio locador) — anti-patrón a evitar: service locator / reconstrucción de infra en aplicación
- [ ] M-06 (configuración): `action=query` delega en `QueryGraphUseCase` con `data_dir` canónico; ramas no soportadas del DSL → error explícito — anti-patrón a evitar: DSL con ramas muertas silenciosas (P-3)

### Wave 3 — Huérfanos y deuda (W6, W7, W8, W9)

- [ ] M-01 (escritura): `verify_predicates action=imports` falla el predicado con el listado de imports alucinados plantados — anti-patrón a evitar: predicado verde con índice vacío
- [ ] M-04 (eventos): `action=conventions` valida contra convenciones activas del CKG con evidencia (`statement` de la convención) — anti-patrón a evitar: CKG↔CPG solo en el schema (HU-2 sin circuito real)
- [ ] M-03 (dominio): `features/code_analysis/domain/` con 0 imports de `infrastructure/` (test de imports) — anti-patrón a evitar: inversión de capas AR-1
- [ ] M-06 (configuración): test de completitud `EXPECTED_ACTIONS == set(actions de inspect_codebase)` — anti-patrón a evitar: actions huérfanas sin dispatch (patrón AGENTS.md)
- [ ] M-02 (estrategias): cada archivo legacy de `infrastructure/` con decisión PO registrada (borrar o plan con owner) — anti-patrón a evitar: muerto sin dueño (HU-4)





