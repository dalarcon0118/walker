# ADR-WALKER-083 — Flight control: atención, alcance y gate por efecto

- **Estado:** aceptado (2026-11-20)
- **Fecha:** 2026-11-20
- **Relacionado:** ADR-WALKER-080 (plan de vuelo)
- **Consolida:** las decisiones previas de flight control (atención, alcance e intake). ADR-081 y ADR-082 quedan **reemplazados** con marca.
- **Guía operativa:** [FLIGHT-CONTROL.md](FLIGHT-CONTROL.md) (estado actual, resultado esperado e invariantes FC-INV). Este ADR registra el porqué y la arquitectura; **no re-especifica el contrato**.

## Contexto

El flight control acumuló tres decisiones en documentos separados: el contrato de
atención del bloque de plan, el gate de desvío por alcance y el gate de intake.
Separadas se contradicen en los bordes: una explica qué inyectar, otra qué
bloquear y otra dónde vive la política. Este ADR las consolida en una sola
decisión y corrige el defecto de fondo.

Evidencia del defecto:

- El gate vive en el **transporte**: `index.js` tiene 7 bloques
  `ctx.tool.transform` envolviendo `execute` de `edit`, `write`, `shell`, `read`,
  `submit`, `implement_task` y `session_bind`; y **0 usos** de
  `ctx.permission.hook`, `ctx.tool.hook` o `ctx.shell.hook`.
- Clasifica por **nombre de tool** (`MUTATING_TOOLS = ["edit", "write", "shell"]`)
  y por regex (`isWalkerCli`, `shellMutation`), no por efecto.
- **Falso positivo:** `pwd`, `ls`, `git status` y `--help` se bloquean porque
  viajan por `shell`. En `ses_e072cd749ffeAbCvAxovxF4pbr` se bloquearon 4/4
  llamadas read-only antes de declarar sprint; el propio comando de remedio quedó
  bloqueado al llevar un pipe.
- **Falso negativo:** no detecta `python -c "open(...,'w')"`, `make`,
  `npm run build` ni `git checkout`; el cierre puede pasar sin evidencia.

OpenCode V2 ya resuelve el parsing y el permiso: reglas `{action, resource,
effect}` (con `shell` como acción y resources producidos por su scanner, incluso
para compuestos), `permission.hook("evaluate")` que puede cambiar a
`allow|ask|deny`, `shell.hook("create.before")` y `tool.hook("execute.after")`.
Reimplementar eso en el plugin es duplicar el runtime, peor.

## Decisiones

### 1. Contrato de atención

Jerarquía y presupuesto:

| Capa | Contenido | Dónde | Presupuesto |
| :--- | :--- | :--- | :--- |
| L0 | goal, tarea, próximo paso | bloque del plan | ≤ 180 chars |
| L1 | blast radius, complejidad, tests | línea `Baseline` | ≤ 140 chars |
| L2 | preflight completo | tool `preflight` | bajo demanda |
| L3 | decisión del CKG | máximo 1 id | ≤ 60 chars |

- Bloque total **≤ 400 chars**; baseline **≤ 140**.
- Solo los **dos archivos de mayor riesgo** (llamadores × complejidad).
- Se inyecta **solo si cambia la firma** (goal, tarea, scope, next, acceptance,
  baseline). Repetir entrena al modelo a ignorar el bloque.
- Si `preflight` falla o excede timeout, no hay baseline: el bloque cae al plan
  (fail-open).
- **Proyecto de trabajo ≠ ubicación:** resolución `plan.project` → archivos del
  `scope` que existan (`project_for_path`, por prefijo y exigiendo existencia) →
  cwd. Se usa en `preflight`, `run_tests` y `session_bind`.
- Evidencia ambigua (sesión nueva, sin plan): se pregunta **una vez** y la
  respuesta se persiste; no se pregunta si el sprint o el plan ya lo dicen.
- L3: `agent_context(file)` → primera decisión `adr-NNN` cuyo `statement` incluya
  `aplica a: <path>`; cache 60 s; fail-open; con `memory_health.trusted=false` no
  se cita ninguna.

### 2. Gate de alcance

- Rechaza escrituras `edit`/`write` fuera de `scope` **solo si hay scope
  declarado**. Sin scope no se inventa alcance.
- Rechazo accionable: archivo, alcance vigente y comando exacto para ampliarlo.
- Ampliar es **trazado y acotado**: una negociación por archivo, sólo dentro del proyecto de trabajo, sin reescribir el plan (C1 en FLIGHT-CONTROL.md §2.4).
- Fail-open si el plan no se puede leer (walker caído/timeout).
- Default ON; escape `WALKER_FLIGHT_CONTROL_SCOPE=0`.
- Matching: igualdad exacta; sufijo de path con límite; directorio declarado
  (sin extensión o con `/` final) como prefijo con límite.
- Limitación aceptada: los paths dentro de `shell` no se resuelven; este ADR
  clasifica el **efecto global** de la invocación, no atribuye paths arbitrarios.

### 3. Gate por efecto (decisión nueva)

**Principio:** la política se decide donde vive el estado; el transporte solo
traduce.

```
OpenCode runtime
  permission engine (reglas estáticas) ─┐
  shell scanner (compuestos) ───────────┤→ permission.hook("evaluate")
  tool execute ─────────────────────────┘→ execute.before / execute.after
                    │
                    ▼
Adapter walker-flight-control  (traduce Decision; hidrata estado; evidencia)
                    │
                    ▼
Dominio walker: GatePolicy (puro)  intake · scope · step · verify
                    │
                    ▼
Trace / CKG
```

El adaptador no contiene reglas; el dominio no conoce `ctx`. Se testea sin
OpenCode y se porta a otro runtime con otro adaptador.

**Modelo de efecto** (la unidad de decisión no es el nombre de la tool):

| Clase | Ejemplos | Pre-intake | Por qué |
| :--- | :--- | :--- | :--- |
| `read` | `pwd`, `ls`, `cat`, `rg`, `git status/diff/log` | `allow` | no muta el repo |
| `protocol` | `walker3 <p>/session_bind`, `implement_task`, `session_plan` | `allow` | es el remedio |
| `verify` | `pytest`, `run_tests`, `verify_predicates` | `allow` + evidencia | no muta fuente |
| `write` | `edit`, `write`, `printf >`, `sed -i`, `rm` | `deny` con remedio | invariante de intake |
| `unknown` | `python -c`, `make`, `npm install`, binario propio | `ask` (headless: `deny` con remedio) | no se puede probar read-only |

**Regla dura:** `unknown` nunca se degrada a `read`.

**Contrato de dominio:**

```python
@dataclass(frozen=True)
class Invocation:
    action: str                  # "shell" | "edit" | "read" | ...
    resources: tuple[str, ...]
    tool: str
    session_id: str
    cwd: str

Decision = Allow | Ask(reason, remedy) | Deny(rule_id, reason, remedy)

def evaluate(inv: Invocation, state: GateState, rules: RuleSet) -> Decision: ...
```

Reglas ordenadas por especificidad; un `Deny` explícito es final; todo
`Deny`/`Ask` lleva `rule_id` y un `remedy` ejecutable (sin remedio sería un
deadlock). La composición intake → scope → step → cierre toma la decisión más
restrictiva.

**Seams:**

| Responsabilidad | Seam |
| :--- | :--- |
| Política estática (read-only, destructivo, red/paquetes) | `permissions` en `opencode.json(c)` |
| Política dinámica (sprint, tarea, scope, paso) | `ctx.permission.hook("evaluate")` |
| Observación de shell | `ctx.shell.hook("create.before")` |
| Evidencia `wrote`/`verified` | `ctx.tool.hook("execute.after")` |
| Contexto inyectado | `ctx.session.hook("context")` (sin cambios) |

`tool.transform` deja de usarse para política. No se parsea dos veces: el scanner
del runtime ya produce los resources de comandos compuestos.

**Semántica de fallo** (tres casos que hoy comparten un `catch`):

- **Disponibilidad** (walker caído/timeout): fail-open, con `plugin_degraded`.
  Las reglas estáticas siguen protegiendo.
- **Política** (`unknown`): fail-closed con remedio, o `ask` si hay humano.
  Nunca `allow` silencioso.
- **Error interno del hook:** fail-open para no tumbar la sesión, pero se cuenta
  y se traza. Un fallo sin rastro es indistinguible de "no pasó nada".
- En headless/eval, `ask` se mapea a `deny` con `message`, explícito.

**Observabilidad:** cada decisión emite `gate.decision` con `action`,
`resources` (redactados), `effect`, `decision`, `rule_id`, `source`,
`latency_ms`, `policy_version`. Hoy solo se traza el deny, así que la tasa de
falsos positivos es invisible.

**Verbos de protocolo:** `walker3 <p>/<tool>` se reconoce por estructura
(tolerando `cd … &&`, `env`, `timeout`, pipes), no por regex de string exacto; la
vía preferida es la tool MCP. El cortacircuito de 2 fallos → `forceOpen` se
conserva.

## Invariantes

- **INV-01:** un solo bloque de contexto por turno; sin duplicación.
- **INV-02:** el bloque ≤ 400 chars; no se repite si la firma no cambió.
- **INV-03:** hechos del proyecto antes que instrucciones.
- **INV-04:** nunca se asume el proyecto de ubicación para archivos de otro repo.
- **INV-05:** un fallo de walker degrada el bloque, no rompe la sesión.
- **INV-06:** toda escritura está dentro del scope o el scope se amplió antes.
- **INV-07:** el gate no reescribe el plan; sólo agrega el archivo que el agente intentó escribir, una vez y trazado. Nunca inventa alcance.
- **INV-08:** sin scope declarado no hay bloqueo de alcance.
- **INV-09:** el rechazo es determinista y no depende del modelo.
- **INV-10:** ninguna escritura sin sprint y tarea declarados.
- **INV-11:** ningún comando demostrablemente read-only se bloquea.
- **INV-12:** disponibilidad fail-open; política fail-closed/ask; nunca allow silencioso.
- **INV-13:** toda decisión se traza con `rule_id` y `policy_version`.
- **INV-14:** el dominio no importa infraestructura ni conoce `ctx`.
- **INV-15:** los verbos de protocolo siempre son alcanzables; un gate insatisfacible no deja la sesión muerta.
- **INV-16:** `unknown` no se trata como `read`.

## Métricas por fase

**Fase F0 — Observabilidad (sin cambiar comportamiento):**
- [ ] M-01 100% de invocaciones mutantes emiten `gate.decision` (allow y deny) — anti-patrón a evitar: trazar solo las denegaciones
- [ ] M-02 p95 de la decisión < 5 ms con estado cacheado — anti-patrón a evitar: spawn de un proceso por comando
- [ ] M-03 `readonly_blocked / readonly_attempts` medido y publicado por sesión — anti-patrón a evitar: no instrumentar los `allow`

**Fase F1 — Extracción de `GatePolicy` al dominio:**
- [ ] M-04 `features/workflow/domain/gate/` no importa infraestructura (test de imports) — anti-patrón a evitar: regex de shell en el adaptador
- [ ] M-05 ≥ 30 fixtures de comandos con efecto esperado, 100% verdes — anti-patrón a evitar: testear solo el wiring de hooks
- [ ] M-06 32/32 modos de `suite.mjs` verdes — anti-patrón a evitar: borrar modos al migrar

**Fase F2 — Política declarativa + `permission.hook`:**
- [ ] M-07 `pwd`, `ls`, `git status`, `--help` → allow; `printf > x` → deny; `walker3 …/session_bind` → allow — anti-patrón a evitar: allowlist por string exacto
- [ ] M-08 0 sesiones con más de 2 round trips de remedio — anti-patrón a evitar: deny sin comando exacto
- [ ] M-09 digest de política idéntico walker↔plugin en 100% de sesiones — anti-patrón a evitar: dos tablas sin contrato

**Fase F3 — Limpieza y portabilidad:**
- [ ] M-10 `MUTATING_TOOLS`, `isWalkerCli` y `shellMutation` eliminados (LOC antes/después medido) — anti-patrón a evitar: dejar código muerto
- [ ] M-11 el ADT se evalúa con un adaptador fake sin `ctx` — anti-patrón a evitar: acoplar el dominio al runtime
- [ ] M-12 0 escrituras sin tarea en corpus adversarial (`>`, `python -c`, `sed -i`, `git checkout`) — anti-patrón a evitar: confiar en el regex

**Métricas de regresión del contrato vigente:**
- [ ] M-13 bloque ≤ 400 chars y 0 reinyecciones sin cambio de firma — anti-patrón a evitar: repetir el bloque
- [ ] M-14 0 escrituras fuera de scope sin ampliación registrada — anti-patrón a evitar: auto-expand silencioso
- [ ] M-15 0 citas de `adr-` con `memory_health.trusted=false` — anti-patrón a evitar: citar autoridad inexistente

## failure_scenarios

| # | Fallo | Causa raíz | Mitigación | Detección |
| :--- | :--- | :--- | :--- | :--- |
| FS-1 | Un compuesto (`a && b`, `a \| b`) evalúa solo el primer resource y deja pasar una escritura | composición parcial | evaluar todos los resources y aplicar el más restrictivo | test diferencial contra el scanner nativo + M-12 |
| FS-2 | Una versión nueva de OpenCode cambia el scanner y clasifica `pwd` como `ask`/`deny` | acoplamiento a semántica del runtime | pinear versión; `observe` como canary; reglas read-only explícitas | M-03 y alerta si `readonly_blocked > 0` |
| FS-3 | `permission.hook` no existe o lanza en una versión dada | diferencia de capacidades | detección en `setup`; fallback a reglas declarativas; traza `degraded` | campo `policy_seam` en `flight_control_active` |
| FS-4 | El remedio vuelve a quedar bloqueado (deadlock de intake) | verbo de protocolo clasificado como `unknown` | `protocol` como clase de primera clase; matching estructural; cortacircuito de 2 fallos | M-08 + modos `bind`/`autodeclare` |
| FS-5 | Walker caído a mitad de una decisión | timeout/CLI roto | fail-open solo por disponibilidad; reglas estáticas activas | `gate_timeout` + `plugin_degraded` |
| FS-6 | Drift entre política del dominio y del adaptador | dos implementaciones/versiones | artefacto con digest; evaluación en sombra durante `observe` | `gate.policy_version` distinto |
| FS-7 | Scope declarado demasiado angosto: el trabajo real cae fuera y cada escritura es un round trip | scope estático por tarea | ampliación explícita en una llamada (`session_plan mode=set/add`) y traza | M-14 + `flight_control_scope_expanded` |
| FS-8 | El bloque de atención crece o se repite y el modelo lo ignora | presupuesto no respetado o firma inestable (baseline incluido) | ≤ 400/140 chars; firma sin baseline; una sola inyección por cambio | `session_metrics` (chars, inyecciones) |
| FS-9 | Proyecto de trabajo mal resuelto: sesión en `game_reset` editando `walker` consulta el CKG equivocado | `project_for_path` por prefijo sin exigir existencia | resolver por plan → scope existente → cwd; exigir `existsSync` | baseline con `project_id` en `intake_baseline` |
| FS-10 | Se cita un `adr-` de un CKG vacío como autoridad | `memory_health.trusted=false` no respetado | no citar con memoria degradada; decir "CKG degradado" | `memory_health` en `agent_context`/`preflight` |

## Alternativas consideradas

| Alternativa | Por qué NO |
| :--- | :--- |
| Ampliar el regex del allowlist | El shell es ilimitado (`env`, `command`, `timeout`, `sh -c`, heredocs, `$()`); cada excepción agrega deuda y el falso negativo persiste |
| Parser propio en el plugin | Duplica el scanner del runtime; dos fuentes de verdad y drift inevitable |
| Llamar a walker por cada comando sin caché | Latencia de proceso por invocación; el estado ya está cacheado a 3–10 s |
| Mantener `tool.transform` como seam principal | Acopla la política al nombre de la tool, ignora permisos y duplica wrappers MCP/CLI |
| Migrar de runtime antes de extraer la política | Hereda el mismo defecto en un runtime más grande; el ADT es lo que hace portable la decisión |

## Plan de implementación

1. **F0 — Observar.** `gate.decision` en modo `observe`. Cerrar con M-01..M-03.
2. **F1 — Extraer.** `GatePolicy` puro + tabla de efectos + fixtures; el adaptador
   traduce con un mapper y todavía usa `transform` para probar equivalencia.
   Cerrar con M-04..M-06.
3. **F2 — Declarar + hook.** Reglas estáticas a `permissions`; `permission.hook`;
   modos nuevos `readonly`, `compound`, `unknown`, `ask`. Cerrar con M-07..M-09.
4. **F3 — Limpiar.** Eliminar regex/allowlist; transform solo para evidencia si
   `execute.after` no alcanza. Cerrar con M-10..M-12.

Cada fase commitea y declara verde su checklist; sin métricas verdes no se
declara completa (AGENTS.md).

## Checklist de segundo orden

1. **¿Qué módulos importan esto?** `walker-flight-control`, el nuevo
   `features/workflow/domain/gate/` y el composition root. CPG/Delta no se tocan.
2. **¿Qué side effects dispara?** Ninguno nuevo: cambia el punto de decisión, no
   el efecto. Se agregan eventos de traza.
3. **¿Qué eventos publica?** `gate.decision`, `gate.policy_version`,
   `gate_timeout`. `flight_control_denied` queda como derivado por compatibilidad.
4. **¿Qué tests dependen?** Los 32 modos de `suite.mjs`, el contrato de 46 tools,
   el perfil `agent` (19.581→9.249 chars) y los tests de workflow. Solo cambian
   los casos read-only, que pasan de deny a allow.
5. **¿Qué cambia en MCP adapter, CLI, CKG, DeltaGraph, Trace?** MCP y CLI no
   cambian; CKG registra la decisión y este ADR; Trace gana `gate.decision`;
   Delta no se toca.

## Consecuencias

- Un solo documento para el flight control; se eliminan las contradicciones entre
  atención, alcance e intake.
- El gate se vuelve más preciso y más barato: desaparece el regex y se usa el
  scanner del runtime.
- La política queda testeable sin OpenCode y portable a otro runtime.
- La tasa de falsos positivos deja de ser invisible.
- Costo: una fase de instrumentación y otra de extracción antes de cambiar
  comportamiento. No es un hotfix; es mover una responsabilidad de capa.

## Limitaciones conocidas

- El runtime sigue siendo la fuente de verdad del parsing de shell; un bug del
  scanner afecta al gate (mitigado con versión pineada y canary).
- `ask` requiere interacción humana; en headless se mapea a deny con remedio.
- El alcance por path de comandos arbitrarios sigue siendo heurístico: se
  clasifica el efecto global, no se atribuyen paths dentro de `shell`.
- El scope es estático por tarea; cambiarlo exige ampliarlo explícitamente.
- El cache de plan (10 s) puede tardar en ver una edición externa.
