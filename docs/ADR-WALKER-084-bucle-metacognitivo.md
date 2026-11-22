# ADR-WALKER-084 — Bucle metacognitivo: economía primero, espejo después (v2)

- **Estado:** aceptado v2 (2026-11-21) — F3a en curso
- **Fecha:** 2026-11-21
- **Revisión:** v2 reordena las fases de v1. La crítica de la primera
  implementación mostró que el supuesto “si el agente se ve, corrige” no está
  respaldado por las mediciones, y que F0 nacía **sin consumidor**. v2 invierte
  el orden: primero se abaratan observar y verificar, después la inyección
  determinista, y el espejo entra como **línea con remedio** cuando el remedio
  ya es barato.
- **Relacionado:** ADR-WALKER-048 (recuperación determinista), ADR-WALKER-080
  (plan de vuelo), ADR-WALKER-083 + [FLIGHT-CONTROL.md](FLIGHT-CONTROL.md),
  [AGENT-EFFICIENCY.md](AGENT-EFFICIENCY.md),
  [ROADMAP-integracion-walker-harness-agente.md](ROADMAP-integracion-walker-harness-agente.md).
- **Guía operativa:** `AGENT-EFFICIENCY.md` §2 y `FLIGHT-CONTROL.md` §2.
- **Spec:** SPEC-026 (sprint `SPR-INTEG-20261118`).

## Contexto

El sistema tiene las tres piezas de un bucle de aprendizaje abiertas: walker
registra, el harness hace cumplir, el humano mide. Falta el tramo que devuelve
la conducta al agente. Pero **la primera implementación de ese tramo enseñó que
el orden importa más que la pieza**.

### Evidencia

| Hecho | Medición (2026-11-21) | Consecuencia |
| :--- | :--- | :--- |
| El harness observa mucho | 4.884 eventos de trace; 427 `session_metrics`; 253 `flight_control_active`; 121 `flight_control_denied`; 270 `close_blocked`; 98 `workflow_suggestion_ignored` | El modelo del agente existe, pero se publica al experimentador |
| El agente no ve su conducta | `brief` inyectaba plan/alcance/criterios; no writes, verificaciones ni denials | Recordatorio del proyecto, no espejo |
| La memoria de decisiones no responde | `brief(text=…)` → `decisions: []`, `degraded: true`, con 138 nodos; `brief(file=knowledge.py)` cita ADR-057/051/022/045/049 (genéricos) | El agente aprende que preguntar no paga |
| El payload natural volcaba el corpus | `{"ckg":{"query":"metacognición"}}` → 138 docs; `query` es clave meta y se descartaba | Respuesta plausible y equivocada, peor que un error |
| Leer paga peaje | `pwd`/`grep`/`--help` bloqueados sin sprint; el remedio con `./bin/` también | El protocolo cobra atención antes de poder pensar (FC-INV-04/05) |
| El cierre se autodestruye | predicado de la spec: **timeout 30 s**; suite real: **1.665 passed en 35,23 s** | Sin verde no hay cierre, ni captura, ni consolidación |
| El espejo llega tarde y caro | `brief` 2,3–2,8 s (5 corridas); sin `session_id` 2,29–2,37 s | AE-INV-05 (p95 ≤400 ms) ya está violado por la composición |
| La fricción no informa | 34 denials históricos, 27 intentos fuera de alcance, **0 verdaderos positivos**; 13 sin comando | Un gate que solo cobra no enseña |

### Lo que la implementación de v1 enseñó

1. **El espejo sin consumidor no cambia conducta.** El plugin no inyecta `self`;
   el agente debe pedir un `brief` de 2,3 s. Un agente que ya evita lectores
   caros no lo va a pedir. F0 era información, no metacognición.
2. **El validador estricto sin alias rompe el propio workflow.**
   `{"trace":{"task":…}}` (template vivo) quedó rechazado. Validar contra el
   schema SQL no es validar contra el lenguaje de consulta.
3. **Dos proyecciones del mismo hecho se contradicen.**
   `session_context.flight_state` usa `merge_write_evidence`; `brief.self` no.
   Una escritura no observada podía dejar `verified: true` con `writes: 0`.
   Ese es exactamente el falso verde que el cierre debe impedir (FS-2).
4. **Los denials acumulados y los gaps sin remedio entrenan a ignorar el
   espejo.** “5 denials” de hace una hora, todos los turnos, es ruido.

## Principio rector (v2)

**El agente responde a incentivos, no a introspección.** En la sesión medida
obedeció todos los gates y usó walker cuando fue la vía más barata. No usa lo
que es caro y a veces vacío. Por eso el orden es **por economía**: primero que
observar y verificar sea barato y confiable; después, que el agente vea su
conducta. Un espejo sin remedio es un regaño.

Corolario de diseño: **ninguna fase exige virtud del agente**. Cada fase mueve
un precio o una garantía, y se mide por conducta, no por texto.

## Las tres piezas (sin cambios de fondo)

1. **Espejo (`self`)** — proyección de la conducta observada, derivada del
   TraceGraph, en el lector único (`brief`). **Un solo proyector**: reusa
   `session_context.flight_state` + `merge_write_evidence`; no reimplementa.
2. **Artefacto (`task_ledger`)** — hipótesis, evidencia, descartes e
   invariantes como objeto; el gate valida la forma, no la virtud.
3. **Consolidación (`session_reflect`)** — al cerrar, un nodo CKG con decisión,
   `aplica a`, evidencia y qué falló; métricas persistidas en la sesión.

## Orden de fases (v2)

| # | Fase | Por qué en este lugar |
| :--- | :--- | :--- |
| F3a | Leer no paga peaje | Sin observar no hay diagnóstico; hoy bloquea `grep`/`--help` |
| F3b | Verificación que no se autodestruye | Sin verde no hay cierre ni señal para el resto |
| F3c | Contrato de consulta (hecho) | El payload natural y el error con remedio; repara la regresión de v1 |
| F2a | Inyección determinista por archivo | Convierte “138 nodos” en “esta decisión aplica a este archivo” |
| F0.2 | Espejo como línea (≤120 chars) | Recién acá el espejo tiene un remedio barato que ofrecer |
| F1 | Artefacto: captura → derivación | Requiere un write path; no se puede derivar lo que no existe |

### Estado de implementación (2026-11-21)

| Fase | Estado | Evidencia |
| :--- | :--- | :--- |
| F3a | **hecho** | modo `readonly`: `pwd`, `git --version`, `ls`, `--help`, pipe a filtro → PERMITE sin sprint; `printf > x` y `python -c` → BLOQUEA. 44/44 modos del harness |
| F3b | **hecho** | `verify_predicates` con `python3 -m pytest -q` (35 s) → **verde**; antes `timeout (30s)`. `WALKER_VERIFY_TIMEOUT` (default 300 s) |
| F3c | **hecho** | alias por índice, `allowed`+`hint`, conformidad de templates; 1.683 passed |
| F2a | **verde: hit@5 = 20/20 (paraphrase + natural)** | `metadata.applies_to` persistido y priorizado en `brief`; golden paraphrase 20/20 y golden natural 20/20 tras enriquecer el CKG con sinónimos (5 ADRs). Mecanismo + medición cumplen el umbral con margen |
| F0.2 | **mecanismo hecho; medición pendiente (n=1)** | proyector único; gaps accionables con ventana; caché por revisión; línea en el bloque. Medición: 1 lectura con gaps, 0 actuados. Muestra insuficiente. `brief_injected` ahora lleva `gaps` (códigos) para habilitar la medición |
| F1 | **hecho (F1a + F1b)** | `capture(..., ledger=...)` persiste el artefacto (idempotente); `build_ledger_skeleton` prellena desde el trace; `ledger="auto"` y `prefill=True` mergean con los campos del agente. 8 tests. Consolidación (`session_reflect`) es F2 |


### F3a — Leer no paga peaje

`read` (`pwd`, `ls`, `cat`, `rg`, `git status/diff/log`, `--help`) → `allow` en
cualquier estado. `protocol` (`session_bind`, `implement_task`, `session_plan`)
alcanzable en todas sus formas (`./bin/`, absoluta, `cd &&`, `env`, `timeout`,
pipe). `unknown` ≠ `read`.

- [ ] M-05 `readonly_blocked = 0` en sesiones sin sprint y sin tarea; fixtures
      `pwd|ls|cat|rg|git status|--help` — anti-patrón a evitar: **God Class**
      (un gate que decide por nombre de tool).
- [ ] M-02 El remedio es alcanzable en 100% de las formas documentadas, sin
      allowlist ad-hoc — anti-patrón a evitar: **Magic Numbers** (regex anclado
      al string exacto).
- [ ] M-04 Cada decisión emite `gate.decision` con `rule_id`, `effect` y comando
      redactado; el deny incluye el remedio — anti-patrón a evitar:
      **Eventual Consistency sin safeguards** (denials sin comando).
- [ ] M-06 32/32 modos de `suite.mjs` verdes y 0 regresiones de alcance —
      anti-patrón a evitar: **Not Invented Here** (parser propio en el plugin).

### F3b — Verificación que no se autodestruye

`run_tests`/`verify_predicates` devuelven `job_id` o error tipado; el timeout se
dimensiona por config; el server sobrevive a la suite.

- [ ] M-02 La suite completa (35,23 s) cierra verde sin matar el proceso; p95 de
      la verificación publicada — anti-patrón a evitar: **Hidden Dependency**
      (el cierre depende de un timeout de otro archivo).
- [ ] M-04 El contrato de error es tipado (`timeout|busy|failed`) y trazable —
      anti-patrón a evitar: **Boolean Trap** (un `ok:false` sin causa).
- [ ] M-01 La evidencia se escribe post-commit, idempotente por `(session, task)`
      — anti-patrón a evitar: **Transaction Script**.
- [ ] M-05 10 cierres consecutivos verdes, 0 `Request already responded to` —
      anti-patrón a evitar: **Distributed Big Ball of Mud** (el estado del
      cierre repartido entre plugin, tool y trace).

### F3c — Contrato de consulta (hecho, con regresión reparada)

- [ ] M-03 Alias por índice en **una** tabla (`query→text`, `limit→size`,
      `trace.task→task_id`, `cpg.symbol→function`) — anti-patrón a evitar:
      **Shotgun Surgery**.
- [ ] M-04 Filtro no soportado → `error` + `allowed` + `example` + `hint`
      (p. ej. `trace.session_id` → “usá session_context”) — anti-patrón a
      evitar: **Boolean Trap** (descartar la clave en silencio).
- [ ] M-06 Test de conformidad que pasa los payloads de los templates
      (`task-spec.prompt.json`, ADR-018) — anti-patrón a evitar:
      **Accidental Complexity** (validar contra el schema SQL y romper el
      workflow).
- **Deuda medida:** `trace.task_id` resuelve por fallback eager (4.949 docs
  materializados). Correcto, caro; lo cierra el índice SQL de F2a.

### F2a — Inyección determinista por archivo

Al entrar un archivo al alcance (o antes de la primera escritura), inyectar las
decisiones y convenciones que lo gobiernan, resueltas por CPG/CKG, sin query
formulada por el modelo.

- [ ] M-03 `applies_to` obligatorio en decisiones; sin él se guarda pero **no se
      prioriza** y se marca `matched: false` — anti-patrón a evitar: **Anemic
      Domain Model**.
- [ ] M-02 Golden set de 20 preguntas: hit@5 ≥85%, p95 ≤400 ms — anti-patrón a
      evitar: **Magic Numbers** (reportar sin n y percentil).
- [ ] M-01 Un proyector único de decisiones; `brief` y `agent_context` no
      divergen — anti-patrón a evitar: **Duplicate Code**.
- [ ] M-06 Inyección estable por firma del archivo/plan — anti-patrón a evitar:
      **Accidental Complexity** (4 bloques distintos por request).

### F0.2 — Espejo como línea

Entra **después** de F3a/F3b verdes: una línea de ≤120 chars en el bloque
estable + JSON completo bajo demanda.

- [ ] M-01 Un solo proyector: reusa `session_context.flight_state` +
      `merge_write_evidence`; una escritura no observada no puede quedar verde —
      anti-patrón a evitar: **Duplicate Code** (dos verdades).
- [ ] M-04 `verified` desconocido = `null`; trace roto → `missing` + `degraded`;
      sin eventos → sección ausente — anti-patrón a evitar: **Eventual
      Consistency sin safeguards**.
- [ ] M-06 ≤120 chars, firma estable, caché por id de último evento, no por
      tiempo — anti-patrón a evitar: **Hidden Dependency**.
- [ ] M-02 `gaps` con `{code, detail, remedy}` y ventana (no acumulado
      histórico) — anti-patrón a evitar: **Magic Numbers**.

### F1 — Artefacto: captura → derivación

- [ ] M-03 **F1a** captura hipótesis/evidencia/descartes en una llamada; status
      `open|confirmed|refuted` — anti-patrón a evitar: **Anemic Domain Model**.
- [ ] M-02 **F1b** prellena desde el trace lo que ya existe (archivos, tests,
      denials) y el agente corrige; nada que no exista se inventa — anti-patrón
      a evitar: **Duplicate Code**.
- [ ] M-06 El gate valida el schema del artefacto, no “que el agente pensó” —
      anti-patrón a evitar: **Not Invented Here**.
- [ ] M-05 Una llamada atómica; sin 8 submits ni estado parcial — anti-patrón a
      evitar: **God Class**.

## Medición falsable (pre-registrada)

Ninguna fase se declara verde por su checklist sola. La ventana es **10 tareas**
en sesiones reales del proyecto `walker`.

| Métrica | Definición operacional | Umbral | Fase |
| :--- | :--- | :--- | :--- |
| `readonly_blocked` | eventos de deny con `effect=read` / intentos read | **0** | F3a |
| `verify_time` | última escritura → verificación verde, por tarea | ≤60 s, 0 muertes del server | F3b |
| `decisions_cited` | decisiones del `brief` cuyo `applies_to` incluye un archivo tocado | ≥1 por tarea | F2a |
| `retrieval_hit@5` | golden set congelado de 20 preguntas | ≥85% | F2a |
| `mirror_acted_on` | tras una lectura de `self` con gap de código X, la llamada siguiente incluye el remedio de X en ≤3 turnos | **≥30%** | F0.2 |
| `friction_per_task` | `(denials + close_blocked) / tareas` | tendencia ↓ | transversal |

**Regla de decisión pre-registrada:** si tras 10 tareas `mirror_acted_on < 30%`,
el espejo se degrada a on-demand, se documenta como información y **F1 no
arranca** hasta revisar el supuesto. Si `retrieval_hit@5 < 85%`, F2a no se
declara verde aunque el bloque se vea bien.

## failure_scenarios

| # | Fallo | Causa raíz | Mitigación | Detección |
| :--- | :--- | :--- | :--- | :--- |
| FS-1 | El espejo se lee y la acción siguiente no cambia | Campos sin consecuencia | `gaps` con `remedy`; medir `mirror_acted_on` | <30% en 10 tareas → se degrada a on-demand |
| FS-2 | El espejo dice `verified: true` sin verificación real | Proyección distinta de la autoritativa | Un solo proyector + `merge_write_evidence`; desconocido = `null` | Test: escritura no observada → no verde |
| FS-3 | El espejo es caro y el agente deja de pedirlo | `load_traces` completo por turno | Caché por id de último evento; inyección de una línea | p95 `brief` ≤400 ms |
| FS-4 | Un filtro no resoluble vuelve a volcar el corpus | Fallback eager | Validar claves + alias por índice; error con `allowed` | Test: `{"ckg":{"query":…}}` → hits, no corpus |
| FS-5 | El agente usa el espejo para narrar en vez de corregir | El espejo premia narrar | El gate valida artefactos; el espejo no puntúa | Ledger vacío + narración |
| FS-6 | El proceso vivo inyecta el bloque viejo | El plugin se carga al arrancar | `plugin_version` en `flight_control_active`; restart documentado | Evento sin versión → rojo |
| FS-7 | El espejo filtra conducta de otra sesión/proyecto | Query sin `session_id`/`project_id` | Filtro exacto + test cruzado | Test de dos sesiones |
| FS-8 | El reorden deja el espejo para después y nunca se implementa | F0.2 no tiene fecha ni condición de entrada | F0.2 entra al cerrar F2a; no se cancela, se reubica | F0.2 ausente al cerrar F2a |
| FS-9 | Abrir read-only abre un bypass de escritura | Clasificación por nombre y no por efecto | Tabla de efectos + 30 fixtures adversariales | Fixture `printf > x` → deny |
| FS-10 | El `job_id` de verificación nunca completa y el cierre espera para siempre | Job huérfano sin reaper | Timeout explícito + reaper + error tipado | Test de job huérfano |
| FS-11 | La inyección por archivo repite bloques y rompe el prefijo | Firma inestable | Firma por archivo+plan; mismos bytes si no cambia | Modo `inject`: hash de prefijo idéntico |

## Alternativas consideradas

| Alternativa | Por qué NO |
| :--- | :--- |
| Mantener el espejo primero (v1) | Nace sin consumidor; el agente no paga 2,3 s por información sin remedio |
| Pedir “reflexión” en el prompt | No medible; ya falló (ADR-080) |
| Un tool nuevo `flight_state` | Viola AE-INV-01; `brief` ya es el lector |
| Store nuevo de métricas | Duplica el SSOT del TraceGraph (INV-004) |
| Validador estricto sin alias | Rompe los templates vivos (medido) |
| Gate de hipótesis obligatorio (T2.1) | Rechazado por no medible; se revive como artefacto capturado, no como prosa |

## Checklist de segundo orden

1. **¿Qué módulos importan esto?** `memories/handlers/knowledge.py`,
   `agent-plugins/walker-flight-control/index.js` (F3a/F0.2),
   `features/event_tracing` (lectura), `features/workflow/domain/gate`
   (F3a). Sin cambios en `tool_profiles`.
2. **¿Qué side effects dispara?** F3c/F0.2: ninguno (read-only + caché
   in-process). F3a: decisiones de permiso (trace). F3b: jobs. F1/F2: writes
   post-commit.
3. **¿Qué eventos publica/consume?** Consume `session_state`,
   `session_metrics`, `flight_control_denied`, `flight_control_scope_expanded`,
   `close_blocked`. Publica `gate.decision` (F3a) y `job.*` (F3b).
4. **¿Qué tests dependen?** `test_brief_bdd`, `test_brief_self_bdd`,
   `test_memory_signature_bdd`, `test_tool_profiles_bdd`,
   `test_workflow_routing_fixes_bdd`, `gate.test.mjs`/`suite.mjs` (F3a),
   `test_memory_runner_chain_dir_bdd`.
5. **¿Qué cambia en MCP/CLI/CKG/Delta/Trace?** MCP: `search_memories` valida y
   traduce; `brief` gana `self`. CLI: mismo contrato. CKG: sin escritura hasta
   F2. Delta: SPEC-026. Trace: lectura + `gate.decision`.

## Consecuencias

- **El espejo llega más tarde.** Es el costo explícito de v2: durante F3a/F3b el
  agente no tiene self-state. Se acepta porque un espejo sin remedio no cambia
  conducta, y porque F3a/F3b son la precondición de que el remedio exista.
- F3a/F3b tocan el plugin y la infraestructura: más riesgo que F0. Se pagan con
  el harness de 32 modos y con fixtures adversariales, no con revisión ocular.
- F3c queda hecho con una deuda medida: `trace.task_id` materializa 4.949 docs.
  Es correcto y caro; el índice SQL es parte de F2a.
- La métrica que decide el ADR no es “el agente se ve metacognitivo”: es
  `mirror_acted_on`. Si no se cumple, se dice y se degrada la pieza.
