# Plugin del agente (OpenCode) ↔ walker

El plugin vive en `agent-plugins/walker-flight-control/index.js` y es la capa que
hace que el agente **use** walker sin que se lo pidan. No es el harness: el
harness mide, el plugin entrega.

**Instalación global (recomendada).** OpenCode exige un **directorio** como
entrada de `plugins` ("configured plugin path must be a directory"), así que el
plugin es `<dir>/index.js`. En `~/.config/opencode/opencode.json`:

```json
{
  "plugins": ["/Users/…/walker/agent-plugins/walker-flight-control"]
}
```

Con eso carga en **cualquier** sesión. La fuente sigue en el repo de walker
(versionada); no se duplica. Si se deja además en `.opencode/plugins/` del
proyecto, se carga dos veces: no hacerlo.

**El plugin anterior no cargaba.** `walker-session.js` (V1) exportaba
`WalkerHooks` como named export; OpenCode V2 exige
`export default { id, setup }` y lo rechazaba al cargar:

    failed to load plugin / walker-session.js
    Plugin must export a default definition with an id and an effect or setup function

O sea que, desde V2, todos sus guards, trazas e inyecciones eran código muerto.
El archivo quedó en `.opencode/legacy/walker-session.v1.js` como referencia (los
guards de `.cognitive/` todavía no están portados).

## Contrato CLI (ADR-WALKER-016)

El plugin no habla MCP: invoca el binario.

```bash
bin/walker3 <project>/<tool> '<json>'
# → una línea JSON por stdout
```

`main.py` decide: si el primer argumento tiene `<project>/<tool>` ejecuta la
tool; si no, arranca el server MCP por stdio como siempre. La resolución de
proyecto y los runners son los mismos que registra el server (`memory_tool_runner`
para memorias, `_build_runner` para fase 3).

**Bug medido (2026-11-17):** el binario ignoraba los argumentos y arrancaba el
server; con stdin vacío salía `rc=0` sin imprimir nada. Todas las llamadas del
plugin (trace, gates, CKG, contexto CPG) eran no-ops silenciosos. El agente
nunca veía walker aunque el plugin "funcionara".

## Qué hace hoy (V2, global)

- **Detección de proyecto**: `project_for_path` (registro de walker) sobre
  `ctx.location.directory`; sin proyecto registrado no inyecta y lo deja
  trazado.
- **Señal de vida**: `flight_control_active` con `project_id` y `directory` al
  cargar; sin eso no se distingue "no cargó" de "no había estado".
- **Binding**: pide `workflow_state` al CLI (cache 60 s) y guarda posición,
  tipo de ejecución, campos requeridos y salidas disponibles.
- **Inyección**: en `ctx.session.hook("context")` agrega un bloque
  `[WALKER] Workflow: …` a las instrucciones del sistema de cada llamada al
  modelo. No bloquea nada.
- **Métrica**: al recibir `session.execution.succeeded/failed` traza
  `workflow_state_injected` (con `injections`, `followed`) y
  `workflow_suggestion_ignored` si no hubo `submit`/`transition`.
- **Followed**: envuelve el `execute` de `walker_submit`/`walker_transition` con
  `ctx.tool.transform` (verificado que el transform existe; falta ejercitarlo
  con un submit real).

Escape hatch: `WALKER_FLIGHT_CONTROL=0`.

## Medir cumplimiento

```bash
python3 scripts/workflow_injection_audit.py            # tasa actual
python3 scripts/workflow_injection_audit.py --json     # para agentes
```

Foto al 2026-11-17: **3 sesiones, 3 inyecciones, 100% ignoradas**, todas
triviales (smoke/`opencode run` de prueba). El script exige `--min-sample 20`
antes de recomendar un gate duro: con n=3 no hay base para bloquear.

## Verificado end-to-end (2026-11-17)

Tres subagentes lanzados desde una sesión en `game_reset`:

1. Con el plugin solo en walker → el subagente **usó walker por MCP**
   (`position=START.LIST`) pero el hook **no corrió**: ninguna sesión nueva en
   el trace. `pwd` del subagente: `…/game_reset`. Plugins por ubicación.
2. Instalado global apuntando a un archivo → error del loader: "configured
   plugin path must be a directory".
3. Instalado global apuntando al **directorio** → trace de game_reset
   (1 evento antes) quedó con:

```
flight_control_active        project_id=game_reset  directory=…/game_reset
workflow_state_injected      injections=2
workflow_suggestion_ignored  injections=2
```

Eso es el hook del agente llamando a walker en un proyecto que no es walker.

## Qué falta (fase 3)

Bloquear acciones cuando el workflow no está satisfecho (`edit` sin
`code_context`, `git commit` sin `submit`), con fail-open si walker no responde
y un mensaje que diga el tool exacto para destrabar. Antes de eso conviene medir
con `workflow_suggestion_ignored`: si el agente ya sigue la sugerencia, bloquear
solo agrega fricción.

## Verificar

```bash
# el contrato CLI responde JSON
bin/walker3 walker/workflow_state '{}'

# smoke del plugin sin OpenCode (fake del shell de Bun)
node /tmp/plugin_smoke.mjs   # inyecta [WALKER] Workflow: … y traza los eventos
```

---

## Superficie de tools (`WALKER_MCP_PROFILE`)

El server registra 33 tools (~3.500 tokens de descripción+esquema que el agente
paga en cada turno y entre las que tiene que elegir). Un perfil es la lista
blanca que se sirve en `tools/list`:

| perfil | tools | ~tokens | para qué |
|---|---|---|---|
| `full` (default) | 33 | 3.499 | contrato histórico, compatibilidad |
| `agent` | 10 | 888 | resolver una tarea del workflow |
| `admin` | 26 | 2.286 | arranque/registro de proyectos, mantenimiento de índices |

```bash
WALKER_MCP_PROFILE=agent python3 -m fast_mcp.main
```

El perfil `agent` deja solo el ciclo de trabajo (`workflow_state`, `submit`,
`transition`, `status`), un lector de contexto (`agent_context`), skills
(`search_skills`, `load_skill`), verificación (`run_tests`, `verify_predicates`)
y evidencia (`capture`). Quedan fuera tres clases:

- **duplicados de tools nativas**: `read_code`/`read`, `code_search`/`grep`,
  `code_context`, `inspect_codebase` — el agente ya las tiene y elegir entre dos
  equivalentes es ruido;
- **telemetría**: `trace_push`, `trace_message` — las escribe el plugin, no el
  modelo;
- **mantenimiento**: `build_code_index`, `update_incremental_code`,
  `index_repo`, `extract_knowledge`, `memory_schema`, `documenting_knowledge`.

Un perfil que nombre tools inexistentes loggea warning y arranca igual (no
rompe el boot por un nombre viejo).

---

## El hook conduce el motor (sub-estados `auto`)

El plugin no solo sugiere: cuando el workflow está en un sub-estado
`execution: auto` (o `bootstrap`), llama a `advance` él mismo antes de inyectar.
Esos pasos no requieren criterio del modelo —los corre el motor— y esperar a que
el agente se acuerde era la razón de que **los 7 proyectos registrados
estuvieran clavados en `START.LIST`** (medido 2026-11-17).

```
sesión nueva → workflow_state = START.LIST (auto)
             → plugin llama advance
             → START.SELECT (llm_task) → se inyecta el bloque con los campos
```

Tope: 3 pasos automáticos por turno (`MAX_AUTO_STEPS`), y si `advance` falla se
corta en silencio (fail-open): un workflow roto no debe bloquear la sesión.

El bloque inyectado trae el comando exacto:

```
[WALKER] Workflow: START.SELECT (llm_task). Campos requeridos: project_id, decision.
Complete el paso ANTES de escribir código: walker3 <proyecto>/submit '{"project_id":"...","decision":"..."}'.
Después, avance con: walker3 <proyecto>/transition '{"gate":"project_selected"}'.
```

Escape hatch: `WALKER_FLIGHT_CONTROL=0`.

Lo que el hook **no** hace: negar herramientas. Un agente puede ignorar la
inyección sin costo (queda como `workflow_suggestion_ignored` en el trace). El
único límite duro hoy es del lado del server: `submit`/`advance` rechazan si
faltan campos requeridos y `transition` rechaza si las precondiciones no se
cumplen.

---

## Gate de negación (opt-in): medido, no adoptado

`WALKER_FLIGHT_CONTROL_DENY=1` hace que el hook niegue herramientas mientras el
paso actual sea `llm_task` con `step_satisfied: false`. Es **opt-in** porque el
costo medido es alto.

Dos variantes, misma tarea trivial ("agregá una línea a a.txt") en un proyecto
con `ANALYZE.ANALYZE` pendiente (`required_fields: ["preconditions"]`):

| gate | resultado | pasos | tokens_in | tokens_out |
|---|---|---|---|---|
| sin gate | editó directo | 4 | 9.735 | 271 |
| `edit`, `write` | **bypass por `shell`** (`echo >>`) | 5 | 4.965 | 370 |
| `edit`, `write`, `shell` | cumplió: submit + transition | 24 | 69.576 | 3.483 |
| idem + mensaje autosuficiente | cumplió | 37 | 88.189 | 5.100 |
| idem + invalidar cache tras submit | cumplió | 20 | 71.473 | 4.121 |

El mensaje más claro y la invalidación de cache **no bajan el piso**: el costo
está en otra parte. Con el gate, un cambio de una línea obliga a recorrer
`ANALYZE → IMPLEMENT.USER_GATE → WORK` completando los campos de cada paso.
El cache aporta (37→20 pasos) porque había denegaciones después de submitear
—el plugin seguía viendo `satisfied: false` por `REFRESH_MS`—, pero el piso
estructural es ~7x tokens sobre la misma tarea.

Lecciones:

- **Bloquear `edit`/`write` no contiene nada**: el agente escribe con `shell`.
  Cualquier gate de herramientas tiene que incluir el intérprete o es decorativo.
- **Con `shell` bloqueado sí cumple**, y no se traba: completa el paso, avanza y
  después edita. Pero la tarea trivial pasa de 5 a 24 pasos (~14x tokens) porque
  el agente primero inspecciona el bloqueo (leyó el fuente del plugin) antes de
  obedecer.
- **Hueco conocido**: los pasos `user_input` no se bloquean (el gate solo mira
  `llm_task`), así que el agente puede atravesar un gate humano sin preguntar.

Conclusión: el gate funciona y el costo es estructural, no de mensajería.
Sirve para forzar el workflow en trabajo donde registrar el proceso vale ~7x
tokens; es absurdo para un cambio de una línea. Opt-in es la posición correcta
mientras no exista una forma de atar el gate a la tarea que el agente está
haciendo (hoy niega cualquier escritura, aunque el paso pendiente sea de otra
tarea).

### Acotado a la tarea declarada

El gate ahora solo aplica si hay una tarea activa: la declara `implement_task`
(task_id + files), que el plugin intercepta. Sin tarea declarada no se niega
nada, y si la tarea declaró archivos, solo se cubren esos archivos.

Medición (n=1 por celda, misma tarea trivial, `DENY=1`):

| corrida | tarea declarada | denegaciones | pasos | tokens_in |
|---|---|---|---|---|
| sin gate (agente ignora la inyección) | no | 0 | 4 | 9.735 |
| gate acotado, sin tarea | no | 0 | 15 | 49.997 |
| gate acotado, con tarea | sí | 0 | 16 | 22.213 |

**El gate no fue el costo: no denegó ni una vez en las dos corridas.** El costo
lo produce la INYECCIÓN: al leer "complete el paso antes de escribir código", el
agente se va a caminar `ANALYZE → IMPLEMENT.USER_GATE → WORK` aunque nadie se lo
impida. Cuando la ignora, la misma tarea son 4 pasos.

Con n=1 y varianza conocida de 0,71x–2,20x no se puede concluir que "con tarea"
sea más barato que "sin tarea"; lo que sí se concluye es que el acotamiento del
gate funciona (0 denegaciones cuando no corresponde) y que **el próximo lever es
la inyección**, no la negación.

### La inyección también va acotada (y es el lever real)

El motor se sigue conduciendo siempre (los `auto` corren server-side, sin
tokens), pero el texto solo se inyecta si hay tarea declarada. Tabla completa de
la misma tarea trivial (n=1, `DENY=1`, estado en `ANALYZE.ANALYZE`):

| corrida | pasos | tokens_in |
|---|---|---|
| sin gate (el agente ignora) | 4 | 9.735 |
| inyección siempre, sin tarea | 15 | 49.997 |
| inyección siempre, con tarea | 16 | 22.213 |
| **inyección acotada, sin tarea** | **4** | **9.582** |
| **inyección acotada, con tarea** | **9** | **11.872** |

Ad-hoc vuelve al baseline (4 pasos, −81% tokens vs inyectar siempre), y cuando
hay tarea el workflow cuesta +22% sobre el baseline: un precio defendible.

Hueco conocido: **bootstrap**. Si la inyección solo aparece con tarea declarada,
nada le sugiere al agente declarar una. En la corrida "con tarea" el prompt lo
pedía explícitamente. Falta un aviso de una sola vez por sesión ("si esto es una
implementación, declarala con `implement_task`") que se apague después.
