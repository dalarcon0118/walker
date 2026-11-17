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
