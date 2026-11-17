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

### Bootstrap: inyecta una vez, y no alcanza

Una sola inyección por sesión invita a declarar la tarea, con permiso explícito
a ignorarla ("si es una consulta o un cambio ad-hoc, ignorá este mensaje").

Medido:

| corrida | pasos | tokens_in | declaró tarea |
|---|---|---|---|
| ad-hoc con bootstrap | 4 | 12.944 | — |
| implementación real (`resta` en un archivo) | 5 | 8.570 | **no** |

El aviso no rompe el baseline ad-hoc (siguen 4 pasos) pero **tampoco logra lo
que busca**: en una implementación real el agente lo ignoró y trabajó directo
(`workflow_suggestion_ignored`, 0 llamadas a `implement_task`).

Conclusión del ciclo completo: la prosa no obliga. Lo único que obligó fue la
negación, y la negación acotada a "hay tarea declarada" depende de que alguien
la declare — que es exactamente lo que el agente no hace por su cuenta. El
círculo se rompe fuera del plugin: la entrada al workflow tiene que ser un gesto
del humano (comando explícito), no una esperanza.

---

## Baseline de intake: llega cuando el agente empieza a mirar código

El agente no pide el baseline. Observado en sesiones reales: abre el archivo y
lee, sin contexto previo ni estrategia. Pedirlo por prompt no funciona (medido:
la inyección se ignora).

Ahora el hook lo calcula **en la primera lectura de archivo de la sesión**:
`agent_context(file)` → impacto (llamadores, llamadas salientes, imports),
peso (los símbolos con mayor complejidad) y decisiones del CKG. Se inyecta en
el turno siguiente, una sola vez.

Costo: una llamada CLI (~1,3 s) y ~150 tokens por sesión. Traza `intake_baseline`
con el archivo y el número de llamadores.

Medido en sesión real sobre `harness/runner.py`:

```
intake_baseline  {"file": "harness/runner.py", "callers": 31}
7 pasos · 31.553 tokens · respuesta correcta
```

Lo que **no** está probado: que el agente use el baseline. La respuesta fue
correcta y no citó el impacto. El baseline es el punto A; la estrategia (punto
B + plan) sigue siendo el nodo ANALYZE del workflow, que requiere que alguien
entre.

---

## Baseline por scope: `snapshot` y `baseline_diff`

El punto A del workflow, en las dimensiones que el CPG conoce, y su comparación
contra el presente.

```bash
walker3 <proyecto>/snapshot '{"scope":"harness/","label":"antes"}'
walker3 <proyecto>/baseline_diff '{"base":"antes"}'
```

`snapshot` devuelve estructura (nodos, archivos, funciones, clases, firmas),
relaciones (CALLS/IMPORTS del scope), complejidad (avg, max, cuántas >10 y el
top-10 con archivo), mapa de archivos (ruta → size:mtime) y dos dimensiones que
hoy no se pueden computar:

- `conventions`: `unavailable` con el motivo (el CKG no se inyecta).
- `defects`: `partial` — `find_bugs` ignora `bug_type` y `dead_code` suele dar 0.

Eso es deliberado: una dimensión que no se puede medir se declara, no se
devuelve como cero.

Con `label` la foto se persiste en `<data_dir>/baselines/<label>.json` (git
ignored); sin label solo se devuelve. La respuesta es liviana (~3 KB): el mapa
completo de archivos queda en el archivo.

`baseline_diff` compara ese archivo contra el estado actual del mismo scope:
archivos agregados/quitados/modificados (por size:mtime), delta de estructura y
relaciones, delta de complejidad y `new_hotspots` —clave (nombre, archivo),
porque hay varias funciones `main` en el proyecto—.

Medido sobre walker, agregando un archivo temporal en `harness/`:

```
summary: 5 cambio(s) en el scope
added:   ["harness/_baseline_probe.py"]
structure: nodes 168→170 · files 10→11 · functions 114→115 · signatures 87→88
complexity: avg 6.80 → 6.77
```

Dos defectos encontrados usándolo: el conjunto de archivos salía de
`.file_snapshot.json`, que NO se refresca en cada build (un archivo nuevo
existía en el CPG y no en el mapa), así que ahora se unen los nodos `file` del
grafo con rutas relativas normalizadas; y el CLI caía al repo de walker con un
`project_id` desconocido —un typo leía y escribía el proyecto equivocado—, ahora
compara el id resuelto y falla ruidosamente.

### Punto A y punto B enganchados al motor

El baseline no depende de que el agente lo pida: son sub-estados `auto`.

```
ANALYZE:  GATHER → BASELINE → OBSERVE → INFO_GATE → ANALYZE → ...
VERIFY:   QA_RUN → PREDICATE_CHECK → BASELINE_DIFF → MONITOR → QA → ...
```

- `BASELINE` (`action: capture_baseline`) corre después de sincronizar el índice:
  guarda la foto con label `task-actual`.
- `BASELINE_DIFF` (`action: diff_baseline`) corre después de los predicados:
  compara contra esa foto y el resultado viaja en `advance().action_result`.

Verificado en un proyecto de prueba (agregando un archivo después de la foto):

```
capture  → ANALYZE.OBSERVE   baselines/task-actual.json
diff     → VERIFY.MONITOR    "5 cambio(s) en el scope"
         added: ["pkg/otra.py"]
         nodes 4→7 · files 1→3 · functions 3→4
         new_hotspot: simple 1→2
```

Trampa que costó un rato: el orquestador rellena `task_id` con el **nombre de la
acción** cuando el workflow no declara uno, así que derivar el label de ahí daba
`task-capture_baseline` y `task-diff_baseline` — dos labels distintos, y el diff
nunca encontraba su base. Ahora el label es fijo (`task-actual`) salvo `label`
explícito.

`R1`: la feature `tasks` no puede importar `code_analysis`, así que el
composition root inyecta la factory con `handlers.baseline.configure(...)`.

### Ancla del contexto: delta → archivo → texto

`agent_context` resuelve el ancla en cascada y **siempre la declara**:

```json
"anchor": {"type": "task", "value": "SPEC-001", "resolved": true,
           "files": ["pkg/logic.py"]}
```

- `task`: la spec del delta (sprint activo o en `planning`), de la que salen
  `objective`, `acceptance_criteria`, `preconditions`, `predicates` y los
  archivos. El primer archivo se usa como scope del CPG.
- `file`: cuando no hay tarea pero el agente ya nombró un archivo. Lleva
  `reason` explicando por qué no se usó la tarea.
- `text`: búsqueda por texto.
- `none`: sin ancla posible, con `reason`. Un contexto vacío sin explicación se
  lee como «no hay nada que saber», que no es lo mismo.

Dos cosas que estaban rotas y que la cascada destapó:

- `SprintService.from_chain_dir` exige un `path_resolver` inyectado; sin él
  devuelve `None`, así que **todo `task_id` parecía inexistente**. Ahora el
  composition root expone `build_sprint_service(chain_dir)`.
- `get_active_spring()` devuelve `None` cuando el sprint está en `planning`, así
  que una tarea recién creada nunca se encontraba. Se busca en el activo y, si
  no hay, en todos los springs.

Estado real medido (2026-11-17): el índice `delta` está **vacío en walker,
backend-microservices y game_reset**, y solo el 0,4% de los eventos de traza
llevan `task_id` (4 de 955). La cascada es correcta, pero hoy el ancla es el
archivo casi siempre, no la tarea.

### Auto-declaración de la tarea en la primera escritura

El delta solo se llenaba cuando alguien llamaba `implement_task`, y el agente no
lo hace. Ahora el hook lo declara solo en la primera `edit`/`write` de la sesión:

```
task_autodeclared  {"task_id":"auto-AwcG2vv55O","file":"b.py"}
agent_context({"task_id":"auto-AwcG2vv55O"})
  anchor {"type":"task","resolved":true,"files":["b.py"]}
  task   {"spec_id":"SPEC-004","objective":"cambios sobre b.py","status":"in_progress"}
```

Detalles que importan: el objetivo se deriva del archivo (no hay prompt
disponible en el hook); la ruta se pasa RELATIVA al proyecto, porque el delta y
el CPG no conocen `/private/tmp`; y las tareas automáticas quedan marcadas con
prefijo `auto-` para poder distinguirlas de las declaradas a mano.

Efecto colateral esperado: al existir `activeTask`, la inyección del workflow y
el gate de negación (si está encendido) empiezan a aplicar desde esa escritura.

### El objetivo de la tarea es el pedido real

El hook `context` recibe `event.messages` (conversación completa). La
auto-declaración toma el **primer** mensaje de rol `user` —no el último, que
puede ser un seguimiento o un resultado de tool—, lo aplana a una línea, lo
corta a 240 chars y lo usa como `objective`:

```
task_autodeclared  objective "Agregá a b.py una función triple(a) que multiplique
                             por tres, con docstring."
                   files ["b.py"] · task_context.task_id auto-ZCqO3IIXba
```

Si la conversación no trae texto de usuario (sesión sin prompt), cae a
«cambios sobre <archivo>» como antes.

De paso, el hook crashaba con `state.data` nulo (`null is not an object
(evaluating 'state.data.position')`) y se llevaba la sesión entera: ahora lee
`state.data?.position`.

---

## Cuando el plugin no carga en una sesión abierta

El plugin se enlaza al crear la sesión: una conversación abierta no recibe los
hooks nuevos aunque se reinicie el servidor (verificado: `flight_control_active`
no aparece y `task_autodeclared` no se emite). Además usa una superficie no
documentada (`ctx.session.hook`, `ctx.tool.transform`, `ctx.event.subscribe`),
que no está en `@opencode-ai/plugin` v1 ni v2.

Dos caminos que **no** dependen de eso:

**Skill de intake** (`skills-src/session-intake`), instalable con
`python3 scripts/hooks_cli.py install skills-src/session-intake`. El agente la
descubre y la carga; probado en sesión nueva: declaró la tarea con el pedido
literal y `empty_layers: []`.

**Comando `/walker-bind`** (`agent-commands/walker-bind.md`, copiar a
`~/.config/opencode/command/`). Ata la sesión sin crear tareas: resuelve el
proyecto, lee el estado del workflow y toma el snapshot del baseline. Probado:
`tasks: []` (no ensucia el delta) y reporta la posición real.

### Qué API expone realmente el runtime (medido)

Sonda con un plugin mínimo (`{id, setup}`) contra OpenCode 2.3.0:

```
keys(ctx): app,location,options,agent,aisdk,command,credential,event,
           experimental,generate,model,provider,integration,mcp,permission,
           plugin,reference,rpc,skill,storage,tool,vcs,websearch,worktree,session,shell

session:  hook, list, create, get, remove, switchAgent, switchModel, prompt,
          generate, command, compact, synthetic, interrupt, update, move, wait,
          context, instructions
tool:     reload, list, transform, hook
agent:    get, list, transform, reload
command:  list, transform, reload
event:    subscribe
experimental: terminal, generate, artifact
plugin:   list            ← sin add/remove en este runtime
```

Consecuencias:

- **`experimental.chat.system.transform` no es alcanzable.** Ese hook pertenece
  al contrato `Plugin = (input) => Hooks` de `@kilocode/plugin`, y este loader
  **rechaza en silencio** esa forma: un plugin exportado como función no carga
  (probado: ni `setup` ni el cuerpo de la función se ejecutaron). Solo se
  acepta `export default {id, setup}`.
- `ctx.session.hook`, `ctx.tool.transform`, `ctx.event.subscribe` **son** la API
  del runtime, no un atajo: simplemente no están en los `.d.ts` publicados.
- `setup` corre **una vez por ubicación**: un proceso con tres worktrees abiertos
  ejecutó tres `setup`, cada uno con su `location.directory`. Por eso el plugin
  resuelve el proyecto por directorio.
- Sin `plugin.add/remove` no hay forma de que un plugin se recargue a sí mismo.
