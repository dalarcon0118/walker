# Flight control — estado, resultado y reglas

- **Rol:** fuente única de la política del flight control (plugin ↔ workflow).
  Es una **instrucción de trabajo**, no un registro histórico.
- **Vigencia:** v1 · 2026-11-20. Reemplaza como guía operativa a ADR-081,
  ADR-082 y ADR-083 (ver §1.6).
- **Cómo leerlo:** §1 son hechos verificables (con archivo:línea o medición
  reproducible); §2 es la definición de terminado; §3 son invariantes.
  Si el código contradice §2 o §3, **el código es la deuda**.
- **Regla de oro:** ninguna afirmación de este documento se completa de memoria.
  Cada una se puede verificar con el comando que se indica.

---

## 1. Estado actual (contexto)

### 1.1 Runtime y superficie real

- El runtime es **OpenCode v2.3.2** (`opencode --version`). El plugin vive en
  `agent-plugins/walker-flight-control/index.js` (un solo archivo, ~2.100 líneas).
- El `ctx` del plugin expone, verificado contra el binario:
  `session.hook(nombre, cb)`, `tool.transform(cb)`, `tool.hook(nombre, cb)`,
  `shell.hook(nombre, cb)`, `permission.hook(nombre, cb)`, `event.subscribe`,
  `session.context`, `session.synthetic`.
- Los seams que el runtime ya implementa y dispara:
  `permission.hook("evaluate")`, `shell.hook("create.before")`,
  `tool.hook("execute.after")` / `("execute.before")`, `session.hook("context")`.
  Verificación: `grep -a -o "create.before\|execute.after\|permission.hook" $(which opencode)`.
- El `permission.hook("evaluate")` recibe un objeto con `action` y `resources`, y
  el evaluador interno resuelve reglas `{action, resource, effect}` con precedencia
  de `deny`. En el binario la acción de esas reglas aparece como `"tool.use"`: el
  vocabulario exacto para `shell` **no está confirmado** (ver §1.7).
- **No hay tipos del API v2.** `~/.config/opencode/node_modules/@opencode-ai/plugin`
  es 1.14.29 (V1: `permission.ask`, `tool.execute.before`, `shell.env`) y **no
  declara** ninguno de los seams que el plugin usa. Hoy la única validación es
  `node --check` (sintaxis): un seam mal escrito se descubre en runtime.

### 1.2 Dónde decide hoy el gate

- Seis bloques `ctx.tool.transform` envuelven `execute`:
  `index.js:1237` (shell), `:1520` (`MUTATING_TOOLS` = `edit|write|shell`,
  `:59`), `:1861` (edit/write), `:1912` (read), `:1963` (`implement_task` en
  `:1967`, `run_tests`/`verify_predicates` en `:1992`, `session_bind` en `:2049`,
  `session_plan` en `:2071`), `:2097` (`submit`/`transition`).
- Clasificación por **nombre de tool** y **regex** sobre el comando:
  `MUTATING_TOOLS` (`:59`), `isWalkerCli` (`:1536`), `shellMutation` (`:377`).
- **0 usos** de `ctx.permission.hook`, `ctx.tool.hook` y `ctx.shell.hook`.
  El runtime ya resuelve parsing y permisos; el plugin los reimplementa.

### 1.3 Qué hace hoy, en orden

1. **Gate de intake (bind):** sin sprint ligado, bloquea todo `MUTATING_TOOLS`
   (incluye `shell`) salvo que el comando sea **exactamente** una invocación de
   `walker3` — el regex está anclado con `$` (`:1538`). Traza
   `flight_control_denied {tool, gate:"bind"}` **sin el comando** (`:1552`).
2. **Gate de intake (tarea):** con sprint ligado y sin tarea declarada, bloquea
   `MUTATING_TOOLS` con un remedio (`implement_task`, `:1620`). Si el **plan**
   ya declara tarea, se auto-declara (`:1580`). Traza `{tool, gate, file}`;
   para `shell` el `file` va vacío: tampoco registra el comando.
3. **Gate de alcance:** si hay `scope` declarado y el archivo cae fuera,
   **negocia una vez por archivo** con `session_plan mode=add` (`:1665`) y traza
   `flight_control_scope_expanded` (`:1678`). Si walker no responde, bloquea con
   el comando exacto para ampliar (`:1691`). Sin `scope` no bloquea.
4. **Gate de cierre:** exige verificación verde si hubo escritura. La
   verificación se hidrata del trace (P1.11) y la escritura se completa con el
   diff del watcher por sesión (P1.12/P1.13).
- Cachés: estado del workflow 60 s (`REFRESH_MS`, `:37`), plan 10 s, binding 3 s,
  `forceOpen` tras 2 fallos de bind o de declaración.
- Escapes: `WALKER_FLIGHT_CONTROL_INTAKE=0`, `_SCOPE=0`, `_DENY=1`.

### 1.4 Medición (sesiones reales, harness excluido)

Reproducible con `python3 scripts/flight_control_metrics.py walker --json`.

- 2.555 eventos; **34 denials en 4 sesiones**: `scope/edit` 17, `bind/shell` 13,
  `scope/write` 3, `bind/edit` 1.
  - Los 20 de `scope` son anteriores a P1.10: hoy el sistema negocia y hay
    **6** `flight_control_scope_expanded`. La ventana actual tiene **0 denials**.
  - `ses_e072cd749ffeAbCvAxovxF4pbr`: 4 denials, todos `bind/shell`, **sin el
    comando registrado**: "eran read-only" es plausible, no demostrable.
- 255 `close_blocked` (nudges de cierre) en el historial: la fricción dominante
  **no** es el gate, y ya está atacada por P1.11–P1.13 (1 nudge en la ventana).
- 0 `flight_control_autodeclared` fuera del harness: el auto-declare desde el
  plan **no está probado en una sesión real**.
- Verificación: suite **1.515 passed**; harness **32/32 modos** en ~190 s;
  `tests/test_tool_profiles_bdd.py` mantiene el perfil `agent` bajo 50%.

### 1.5 Documentos y memoria

- ADR-081 (gate de alcance) y ADR-082 (contrato de atención) **fueron borrados
  del árbol** (sin commit). ADR-083 los consolida, está **untracked** y en estado
  `propuesto`; `SPEC-WALKER-044:6` y `index.js:51` ya lo citan.
- CKG del proyecto: **1 nodo** cita `adr-082` y no tiene `superseded_by`;
  ADR-083 no está indexado. `search_memories` no lo encuentra.

### 1.6 Contradicciones abiertas (deuda explícita)

| # | Documento dice | Código/harness hace |
| :-- | :-- | :-- |
| C1 | ADR-081 INV-2 / ADR-083 §2 e INV-07: "ampliar es explícito, el gate no modifica el plan". | El plugin negocia solo (`:1665`) y el modo `scope` del harness exige `edit fuera → PERMITE` con `plan ampliado: SI`. |
| C2 | INV-11: "ningún comando demostrablemente read-only se bloquea". | `pwd`, `ls`, `git status`, `--help` van por `shell` y se bloquean antes de ligar/declarar (`:1520`, `:1538`). |
| C3 | "El cierre no pasa sin evidencia". | `shellMutation` (`:377`) no detecta `python -c "open(...,'w')"`, `make`, `npm run build` ni `git checkout`: la escritura no deja deuda. |
| C4 | M-02 del ADR: "p95 de la decisión < 5 ms con estado cacheado". | No hay contrato para el **cache frío**: no está definido qué devuelve el gate con estado vencido. |

### 1.7 Lo que NO se sabe (no inventar)

- El vocabulario exacto de `permission.hook("evaluate")` para comandos de shell
  (`action` = `"tool.use"` vs `"shell"`): requiere capturar un payload real.
- Cuántos de los 13 `bind/shell` eran read-only: el denial no guarda el comando.
- Si `permission.hook` compone con las reglas estáticas por AND o por precedencia
  de `deny`.
- La tasa de falsos negativos de `shellMutation` en uso real (no hay fixture).

---

## 2. Resultado esperado (cómo debe quedar)

Al terminar los cambios, **el sistema cumple todo lo siguiente**. Lo que no se
pueda cumplir no se implementa: se documenta acá y se vuelve a decidir.

### 2.1 La decisión es por EFECTO, no por nombre de tool

| Clase | Ejemplos | Pre-intake |
| :-- | :-- | :-- |
| `read` | `pwd`, `ls`, `cat`, `rg`, `git status/diff/log`, `--help` | `allow` |
| `protocol` | `walker3 <p>/session_bind`, `implement_task`, `session_plan` | `allow` |
| `verify` | `pytest`, `run_tests`, `verify_predicates` | `allow` + evidencia |
| `write` | `edit`, `write`, `printf > x`, `sed -i`, `rm` | `deny` con remedio |
| `unknown` | `python -c`, `make`, `npm install`, binario propio | `ask`; headless: `deny` con remedio |

- Un comando `read` **nunca** se bloquea, ni antes de ligar ni antes de declarar.
- Un verbo de `protocol` es **siempre alcanzable**, incluso dentro de
  `cd … &&`, `env …`, `timeout …` o con un pipe.
- `unknown` **nunca** se degrada a `read`. En headless, `ask` se mapea a `deny`
  con `remedy`, explícito.

### 2.2 La política es dominio puro; el plugin solo traduce

- `GatePolicy` vive en `fast_mcp/features/workflow/domain/gate/`: no importa
  infraestructura, no conoce `ctx`, se testea sin OpenCode.
- Contrato mínimo:

  ```python
  Invocation = (action, resources, tool, session_id, cwd)
  Decision = Allow | Ask(reason, remedy) | Deny(rule_id, reason, remedy)
  evaluate(inv, state, rules) -> Decision
  ```

- El adaptador: hidrata estado, arma `Invocation`, traduce `Decision`, emite
  eventos. **No contiene reglas.**
- La composición intake → alcance → paso → cierre toma la decisión **más
  restrictiva**; un `Deny` explícito es final.

### 2.3 Seams (los que el runtime ya tiene)

| Responsabilidad | Seam |
| :-- | :-- |
| Política estática (read-only, destructivo, paquetes) | `permissions` en `opencode.json(c)` |
| Política dinámica (sprint, tarea, alcance, paso) | `permission.hook("evaluate")` |
| Observación de shell (incluye compuestos) | `shell.hook("create.before")` |
| Evidencia `wrote`/`verified` | `tool.hook("execute.after")` |
| Contexto inyectado | `session.hook("context")` (se conserva) |

- `tool.transform` deja de usarse para **política**. Si `execute.after` no
  alcanza para la evidencia, se conserva solo para eso y queda justificado acá.
- Antes de escribir el mapper se captura **un payload real** de `evaluate` y de
  `create.before` (ver §2.7 F0). No se diseña sobre nombres supuestos.

### 2.4 Alcance: se conserva la negociación (resuelve C1)

- La negociación automática se **codifica como decisión**: una sola vez por
  archivo, con `session_plan mode=add`, y traza `flight_control_scope_expanded`.
- Sin `scope` declarado no hay bloqueo por alcance (el gate no inventa alcance).
- Si walker no responde, el bloqueo lleva el comando exacto para ampliar.
- Queda prohibido volver al bloqueo por archivo sin negociación: medido, era
  ~20 turnos por sesión.

### 2.5 Observabilidad completa

- Cada invocación emite `gate.decision` con: `action`, `resources` (redactados),
  `effect`, `decision`, `rule_id`, `source`, `latency_ms`, `policy_version`.
- El `flight_control_denied` existente **incluye el comando** (redactado), para
  poder auditar los falsos positivos históricos.
- Se publica `readonly_blocked / readonly_attempts` por sesión.

### 2.6 Semántica de fallo y caché

- **Disponibilidad** (walker caído/timeout): fail-open, traza `plugin_degraded`.
- **Política** (`unknown`): fail-closed/ask con `remedy`. Nunca allow silencioso.
- **Error interno del hook:** fail-open, contado y trazado.
- **Cache frío:** se usa la última política conocida si y solo si incluye la
  tarea y el alcance de la sesión; si no, `deny` con `remedy`. Nunca un `allow`
  por ausencia de estado. La regla se documenta y se testea.

### 2.7 Orden de trabajo y definición de terminado

Cada paso se commitea y declara verde su checklist; sin métricas verdes no se
pasa al siguiente.

1. **F0 · Observar (sin cambiar comportamiento).**
   - Capturar un payload real de `permission.hook("evaluate")` y de
     `shell.hook("create.before")`; anotar el vocabulario definitivo en §2.3.
   - `gate.decision` en modo `observe`; `cmd` redactado en los denials.
   - Verde: 100% de invocaciones mutantes con evento; `readonly_blocked` medido;
     p95 de la decisión < 5 ms con caché tibio; 32/32 modos; suite verde.
2. **F1 · Extraer.** `GatePolicy` puro + tabla de efectos + ≥30 fixtures
   (compuestos, `python -c`, `make`, `git checkout`, `--help`). El adaptador
   sigue usando `transform` para probar equivalencia.
   - Verde: dominio sin imports de infraestructura; fixtures 100%; 32/32.
3. **F2 · Declarar + hook.** Reglas estáticas a `permissions`;
   `permission.hook("evaluate")`; modos nuevos `readonly`, `compound`,
   `unknown`, `ask`.
   - Verde: `pwd/ls/git status/--help` → allow; `printf > x` → deny;
     `walker3 …/session_bind` → allow (incluso con pipe); 0 sesiones con >2
     round trips de remedio; digest de política idéntico walker↔plugin.
4. **F3 · Limpiar.** Eliminar `MUTATING_TOOLS`, `isWalkerCli` y `shellMutation`.
   - Verde: 0 escrituras sin tarea en corpus adversarial; LOC antes/después;
     32/32 modos; suite verde.

### 2.8 Documentos y memoria

- Este documento es la **única** guía de trabajo del flight control.
- ADR-081 y ADR-082 se retiran con `Estado: reemplazado por este documento`
  (no se borran sin dejar la marca) o se archivan; ADR-083 se acepta citando acá
  y deja de ser un documento paralelo.
- En el CKG: nodo de esta decisión indexado y `supersedes` explícito de
  `adr-081`/`adr-082`; 0 nodos activos que citen decisiones retiradas.
- Una sola dirección de referencias: este documento puede citar ADRs, los ADRs
  retirados no se citan desde código ni desde specs.

---

## 3. Reglas que no pueden violarse (invariantes)

Cada regla trae cómo se detecta su violación. Una violación **no se compensa**
con otra regla cumplida.

- **FC-INV-01 · Fuente única.** Solo este documento define política de flight
  control. Ningún otro archivo (plugin, spec, ADR) la redefine.
  *Detección:* dos documentos con la misma regla y distinto texto.
- **FC-INV-02 · Dominio puro.** `features/workflow/domain/gate/` no importa
  infraestructura ni conoce `ctx`.
  *Detección:* test de imports sobre el paquete.
- **FC-INV-03 · Intake.** Ninguna escritura sin sprint y tarea declarados. No se
  relaja; el auto-declare desde el plan cuenta como declaración.
  *Detección:* corpus adversarial + harness `normal`/`autodeclare`.
- **FC-INV-04 · Read-only nunca se bloquea.** La clase `read` de §2.1 es
  `allow` en cualquier estado (sin sprint, sin tarea).
  *Detección:* `readonly_blocked = 0`; fixtures de §2.7.
- **FC-INV-05 · Protocolo alcanzable.** Los verbos de `protocol` se reconocen
  por estructura (prefijos, `&&`, `env`, `timeout`, pipes).
  *Detección:* modos `bind`/`autodeclare` y fixtures con pipe.
- **FC-INV-06 · `unknown` ≠ `read`.** Lo no demostrable es `ask`/`deny`, jamás
  `allow`.
  *Detección:* fixture `python -c`, `make`, `git checkout`.
- **FC-INV-07 · Remedio.** Todo `Deny`/`Ask` lleva `rule_id` y un `remedy`
  ejecutable. Un gate sin remedio es un bug.
  *Detección:* contrato del ADT + tests de cada regla.
- **FC-INV-08 · Alcance.** Sin `scope` declarado no hay bloqueo. Con `scope`,
  fuera de alcance se negocia **una vez por archivo** y se traza; el gate no
  inventa alcance.
  *Detección:* modo `scope` (PERMITE + `plan ampliado: SI`) y
  `flight_control_scope_expanded`.
- **FC-INV-09 · Fallo.** Disponibilidad fail-open con `plugin_degraded`; política
  fail-closed/ask; error interno fail-open con traza. Nunca `allow` silencioso.
  *Detección:* tests de timeout/CLI roto y de excepción del hook.
- **FC-INV-10 · Toda decisión se traza.** Con `rule_id` y `policy_version`; el
  deny incluye el comando redactado.
  *Detección:* `gate.decision` por invocación; `readonly_attempts` > 0.
- **FC-INV-11 · Evidencia.** Una escritura no observada no puede dejar el cierre
  limpio: el diff del watcher por sesión completa `wrote`.
  *Detección:* `task_degraded`/`session_state` con `touched_source=watcher`;
  modo `extverify`.
- **FC-INV-12 · Cierre.** El cierre exige verificación verde; la certificación
  por shell con pipe exige `WALKER_RESULT`. Sin marcador no hay verde.
  *Detección:* modos `close*` y `closeclipipefail`.
- **FC-INV-13 · Atención.** Bloque de plan ≤ 400 chars, baseline ≤ 140; una
  inyección por cambio de firma. Repetir entrena a ignorar.
  *Detección:* modo `inject` (tope) y `gateprompt` (estable sin cambio).
- **FC-INV-14 · Memoria.** Con `memory_health.trusted=false` no se cita ningún
  `adr-`; no se citan decisiones retiradas.
  *Detección:* modo `degraded` + CKG sin nodos activos de 081/082.

---

Si §2 y §3 parecen contradictorios en algún caso, se resuelve en §3 (es la
restricción dura) y se corrige §2 en el mismo cambio.
