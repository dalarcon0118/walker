# Flight control — estado, resultado y reglas

- **Rol:** fuente única de la política del flight control (plugin ↔ workflow).
  Es una **instrucción de trabajo**, no un registro histórico.
- **Vigencia:** v4 · 2026-11-20. Resuelve C1–C4 (alcance, read-only, evidencia
  de cierre, caché) y cierra la reconciliación documental (§2.8). Reemplaza como
  guía operativa a ADR-081 y ADR-082; ADR-083 queda aceptado como registro del
  porqué y delega acá el contrato (ver §1.5).
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
- **Fuera de alcance, en el historial:** 27 intentos (20 denials de `scope` +
  7 `flight_control_scope_expanded`). Los 27 apuntan a archivos del trabajo en
  curso (`session.py`, `knowledge.py`, `_di.py`, tests, docs); **ninguno** a un
  archivo ajeno o basura. Es decir: el bloqueo produjo 0 verdaderos positivos y
  el costo de la vía explícita habría sido ~2 turnos por intento (~54 turnos).
- Verificación: suite **1.515 passed**; harness **32/32 modos** en ~190 s;
  `tests/test_tool_profiles_bdd.py` mantiene el perfil `agent` bajo 50%.

### 1.5 Documentos y memoria

- ADR-081 y ADR-082: `Estado: reemplazado por FLIGHT-CONTROL.md` (se conservan
  con marca; no se borran). ADR-083: `aceptado`; registra el porqué y la
  arquitectura y delega el contrato operativo a este documento.
- CKG: nodo `adr-083` (`status=active`) con `supersedes: [adr-081, adr-082]`;
  `adr-082` quedó `status=superseded` con `superseded_by`, así que no se cita
  como activo. `adr-081` no existía como nodo (`supersede_missing`).
  Verificado con `CkgReadService.query_all` (el superseded sale de la página) y
  por id con `_fetch`.

### 1.6 Decisiones sobre las contradicciones (v2)

**C1 · Alcance — el gate negocia.** ADR-081/INV-07 decían "el gate no modifica el
plan". La medición muestra lo contrario de lo que suponía esa regla: de 27
intentos fuera de alcance, los 27 eran archivos del trabajo en curso y ninguno
era deriva. Decisión en §2.4: se conserva la negociación, acotada a un archivo
por vez, sólo dentro del proyecto de trabajo, trazada y sin reescribir el plan.
FC-INV-08.

**C2 · Read-only bloqueado — el fin es `allow`; el interim se mide, no se
improvisa.** Hoy, sin sprint o sin tarea, se bloquea todo `shell` que no sea
exactamente `walker3` (`:1520`, regex anclado en `:1538`). Hay 13 denials
`bind/shell` en 4 sesiones (el comando no se guarda). Decisión: el resultado
final es `read → allow` (§2.1) y **no se agrega una allowlist ad-hoc**: una lista
incompleta mueve el falso positivo y una lista de más abre el bypass que el gate
existe para cerrar. Hasta F2 se mantiene el bloqueo, F0 registra el comando y
mide `readonly_blocked`, y F2 es el criterio para declarar FC-INV-04 verde.
Lo que sí se arregla en F0: la **alcanzabilidad del remedio** (FC-INV-05). Un
`walker3 … '{}' 2>&1` o con pipe hoy queda bloqueado porque el ancla exige la
línea exacta: el remedio del propio gate es inalcanzable (deadlock). `protocol`
se reconoce de forma estructural (tolera `cd &&`, `env`, `timeout` y
duplicaciones de fd; rechaza cualquier otro compuesto).

**C3 · Falsos negativos del cierre — cubiertos por evidencia, no por regex.**
`shellMutation` (`:377`) no ve `python -c "open(...,'w')"`, `make`,
`npm run build` ni `git checkout`. Pero el cierre no depende de esa regex: la
próxima `session_context` compara el árbol con el snapshot de la sesión y lo
detecta. Reproducido con el handler real: tras un `python -c` que escribe,
`flight_state = {wrote: true, writes: 2, touched: ["b.py", …]}`. El plugin hace
ese force-hydrate antes de nudgar. Decisión: FC-INV-11 (evidencia del watcher) es
la garantía; `shellMutation` queda sólo para clasificación/`firstMutation` y se
elimina en F3, con fixtures de esos comandos en F1.
*Bug encontrado al verificar:* el filtro del footprint no excluía
`*.sqlite3-wal`/`-shm`, así que el motor aparecía como escritura del agente.
Corregido en v2.

**C4 · Cache frío — un fallo no es estado fresco.** `refresh` cachea 60 s y
`readState` devuelve `null` ante un fallo dejando `fetchedAt` actualizado: un
fallo transitorio deja al plugin ciego 60 s, sin reintento. Decisión: tibio =
in-process (<5 ms); frío = una lectura acotada (≤750 ms); fallo = no se cachea
como fresco (reintento ≤2 s), se conserva el último estado conocido de la sesión
si incluye tarea y alcance, y toda decisión por disponibilidad emite
`plugin_degraded` y `gate.decision.degraded=true`. `read` no depende del estado;
un `write` con estado desconocido es **disponibilidad**: allow trazado, nunca
allow silencioso. FC-INV-15.

### 1.7 Lo que NO se sabe (no inventar)

- El vocabulario exacto de `permission.hook("evaluate")` para comandos de shell
  (`action` = `"tool.use"` vs `"shell"`): requiere capturar un payload real.
- Cuántos de los 13 `bind/shell` eran read-only: el denial no guarda el comando.
  F0 lo mide; hasta entonces, el interim de C2 no se dimensiona de memoria.
- Si `permission.hook` compone con las reglas estáticas por AND o por precedencia
  de `deny`.
- La tasa de falsos negativos de `shellMutation` **como clasificador en tiempo
  real** (no hay corpus). El cierre no depende de él: la evidencia es el diff del
  watcher (C3).

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

### 2.4 Alcance: la negociación SE CONSERVA, acotada (decide C1)

**Decisión (v2):** cuando el agente escribe un archivo fuera del `scope`, el
gate **negocia**, no bloquea: agrega ese archivo al plan con
`session_plan mode=add` (una sola vez por archivo) y traza
`flight_control_scope_expanded`. El bloqueo queda sólo para el caso en que
walker no responda.

Por qué, con la evidencia de §1.4: de 27 intentos fuera de alcance registrados,
los 27 fueron archivos del trabajo en curso; el bloqueo no evitó ninguna
escritura ajena y la vía explícita habría costado ~2 turnos por intento. Un gate
que sólo produce falsos positivos no es un contrato: es fricción. Pero la
negociación **no** puede ser un cheque en blanco; queda acotada así:

1. **Un archivo por vez, idempotente.** Si el archivo ya se amplió, no se repite
   (ni se traza dos veces).
2. **Sólo dentro del proyecto de trabajo.** Si el path no está bajo el
   `projectDir` de la sesión, **no se agrega**: se bloquea con el remedio de
   declarar/abrir el proyecto correcto. El plan de un proyecto no absorbe
   archivos de otro. (Hoy `relPath` devuelve el path crudo para archivos
   externos y el gate los agregaría: es un bug a cerrar en F2.)
3. **El gate agrega, no reescribe.** Se usa `mode=add`; el `scope` declarado
   original se conserva intacto y auditable. El gate nunca hace `mode=set`.
4. **Se cuenta y se ve.** El número de ampliaciones de la sesión queda en
   `session_metrics` (`scope_expanded`) y en el reporte. No bloquea por cantidad,
   pero una sesión que amplía mucho es señal de que el plan nació mal: se lee,
   no se adivina.

Sin `scope` declarado no hay bloqueo por alcance (el gate no inventa alcance).
Si walker no responde, el bloqueo lleva el comando exacto para ampliar
(`mode=add` con el archivo), que es una sola llamada.

### 2.5 Observabilidad completa

- Cada invocación emite `gate.decision` con: `action`, `resources` (redactados),
  `effect`, `decision`, `rule_id`, `source`, `latency_ms`, `degraded`,
  `policy_version`.
- El `flight_control_denied` existente **incluye el comando** (redactado), para
  poder auditar los falsos positivos históricos.
- Se publica `readonly_blocked / readonly_attempts` por sesión y por sesión el
  conteo de decisiones `degraded` (disponibilidad) — nunca un allow anónimo.

### 2.6 Semántica de fallo y caché

- **Disponibilidad** (walker caído/timeout): fail-open, traza `plugin_degraded`
  y `gate.decision.degraded=true`. Las reglas estáticas siguen protegiendo.
- **Política** (`unknown`): fail-closed/ask con `remedy`. Nunca allow silencioso.
- **Error interno del hook:** fail-open, contado y trazado.
- **Cache tibio:** decisión in-process, p95 < 5 ms, sin IO.
- **Cache frío:** una lectura acotada (≤750 ms). Se usa el último estado conocido
  de la sesión si y solo si incluye tarea y alcance; si no lo incluye, es
  disponibilidad (allow trazado) o `deny` si hay humano para preguntar.
- **Fallo de lectura:** NO se cachea como estado fresco; reintento con backoff
  corto (≤2 s) para no dejar al plugin ciego 60 s por un error transitorio.
  Nunca un `allow` porque "no hay dato" en el sentido de política.

### 2.7 Orden de trabajo y definición de terminado

Cada paso se commitea y declara verde su checklist; sin métricas verdes no se
pasa al siguiente.

1. **F0 · Observar (sin cambiar comportamiento de política).**
   - Capturar un payload real de `permission.hook("evaluate")` y de
     `shell.hook("create.before")`; anotar el vocabulario definitivo en §2.3.
   - `gate.decision` en modo `observe`; `cmd` redactado en los denials.
   - **Remedio alcanzable (C2):** `protocol` se reconoce de forma estructural
     (`cd &&`, `env`, `timeout`, duplicación de fd). Es un deadlock, no una
     observación: se corrige acá.
   - **Fallo de lectura no cacheado (C4):** un `readState` fallido no marca
     `fetchedAt`; reintento ≤2 s. Evita 60 s de ceguera por un error transitorio.
   - Verde: 100% de invocaciones mutantes con evento; `readonly_blocked` medido;
     p95 de la decisión < 5 ms con caché tibio; 32/32 modos; suite verde.
2. **F1 · Extraer.** `GatePolicy` puro + tabla de efectos + ≥30 fixtures
   (compuestos, `python -c`, `make`, `git checkout`, `--help`). El adaptador
   sigue usando `transform` para probar equivalencia.
   - Verde: dominio sin imports de infraestructura; fixtures 100%; 32/32.
3. **F2 · Declarar + hook.** Reglas estáticas a `permissions`;
   `permission.hook("evaluate")`; modos nuevos `readonly`, `compound`,
   `unknown`, `ask`; caché frío con política last-known (§2.6).
   - Verde: `pwd/ls/git status/--help` → allow (**C2 cerrada**:
     `readonly_blocked = 0`); `printf > x` → deny;
     `walker3 …/session_bind` → allow (incluso con pipe); 0 sesiones con >2
     round trips de remedio; 0 auto-expansiones de archivos fuera del
     `projectDir`; digest de política idéntico walker↔plugin.
4. **F3 · Limpiar.** Eliminar `MUTATING_TOOLS`, `isWalkerCli` y `shellMutation`.
   - Verde: 0 escrituras sin tarea en corpus adversarial; LOC antes/después;
     32/32 modos; suite verde.

### 2.8 Documentos y memoria (ejecutado)

- Este documento es la **única** guía de trabajo del flight control.
- ADR-081 y ADR-082: `Estado: reemplazado por este documento`, conservados como
  registro histórico (no borrados, no citables como política).
- ADR-083: `aceptado`; delega explícitamente el contrato operativo acá y no lo
  re-especifica.
- CKG: `adr-083` activo con `supersedes: [adr-081, adr-082]`; `adr-082`
  `status=superseded`; 0 nodos activos que citen decisiones retiradas.
- Una sola dirección de referencias: este documento puede citar ADRs; los ADRs
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
  `allow` en cualquier estado (sin sprint, sin tarea). Hasta F2 es deuda
  declarada (C2) y se mide; F2 es el criterio para declararla verde.
  *Detección:* `readonly_blocked = 0`; fixtures de §2.7.
- **FC-INV-05 · Protocolo alcanzable.** Los verbos de `protocol` se reconocen
  por estructura (toleran `cd &&`, `env`, `timeout` y duplicación de fd; otra
  composición no se reconoce). El remedio nunca queda bloqueado por el gate.
  *Detección:* modos `bind`/`autodeclare` y un fixture con `2>&1` (PERMITE);
  un `walker3 …; printf > f` no se reconoce (BLOQUEA).
- **FC-INV-06 · `unknown` ≠ `read`.** Lo no demostrable es `ask`/`deny`, jamás
  `allow`.
  *Detección:* fixture `python -c`, `make`, `git checkout`.
- **FC-INV-07 · Remedio.** Todo `Deny`/`Ask` lleva `rule_id` y un `remedy`
  ejecutable. Un gate sin remedio es un bug.
  *Detección:* contrato del ADT + tests de cada regla.
- **FC-INV-08 · Alcance.** Sin `scope` declarado no hay bloqueo. Con `scope`,
  un archivo fuera se negocia **una vez por archivo**, sólo si está dentro del
  proyecto de trabajo, y se traza; el gate **agrega** (`mode=add`) y **nunca
  reescribe** el plan. Un path fuera del proyecto de trabajo se bloquea con el
  remedio del proyecto correcto, no se absorbe.
  *Detección:* modo `scope` (PERMITE + `plan ampliado: SI`),
  `flight_control_scope_expanded` con `file` dentro del proyecto, y un caso
  negativo con path externo (BLOQUEA).
- **FC-INV-09 · Fallo.** Disponibilidad fail-open con `plugin_degraded`; política
  fail-closed/ask; error interno fail-open con traza. Nunca `allow` silencioso.
  *Detección:* tests de timeout/CLI roto y de excepción del hook.
- **FC-INV-10 · Toda decisión se traza.** Con `rule_id` y `policy_version`; el
  deny incluye el comando redactado.
  *Detección:* `gate.decision` por invocación; `readonly_attempts` > 0.
- **FC-INV-11 · Evidencia.** Una escritura no observada no puede dejar el cierre
  limpio: el diff del watcher por sesión completa `wrote`, excluyendo el
  footprint del motor (`.cognitive/`, `*.json.gz`, snapshots, `*.sqlite3` y sus
  `-wal`/`-shm`).
  *Detección:* `task_degraded`/`session_state` con `touched_source=watcher`;
  modo `extverify`; `test_scan_ignora_el_estado_de_walker`.
- **FC-INV-12 · Cierre.** El cierre exige verificación verde; la certificación
  por shell con pipe exige `WALKER_RESULT`. Sin marcador no hay verde.
  *Detección:* modos `close*` y `closeclipipefail`.
- **FC-INV-13 · Atención.** Bloque de plan ≤ 400 chars, baseline ≤ 140; una
  inyección por cambio de firma. Repetir entrena a ignorar.
  *Detección:* modo `inject` (tope) y `gateprompt` (estable sin cambio).
- **FC-INV-14 · Memoria.** Con `memory_health.trusted=false` no se cita ningún
  `adr-`; no se citan decisiones retiradas.
  *Detección:* modo `degraded` + CKG sin nodos activos de 081/082.
- **FC-INV-15 · Caché.** Un fallo de lectura no se cachea como estado fresco: se
  reintenta con backoff corto (≤2 s) y la decisión degradada se traza. Ningún
  `allow` por ausencia de dato puede ser anónimo.
  *Detección:* test de dos lecturas con la primera fallando; `gate.decision`
  con `degraded=true`.

---

Si §2 y §3 parecen contradictorios en algún caso, se resuelve en §3 (es la
restricción dura) y se corrige §2 en el mismo cambio.
