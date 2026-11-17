# Plugin del agente (OpenCode) ↔ walker

El plugin vive en `.opencode/plugins/walker-session.js` y es la capa que hace que
el agente **use** walker sin que se lo pidan. No es el harness: el harness mide,
el plugin entrega.

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

## Qué hace hoy

- **Binding de sesión**: al primer mensaje/tool pide `workflow_state` (en
  background, throttle 60 s) y guarda posición, tipo de ejecución, campos
  requeridos y salidas disponibles.
- **Inyección por turno**: agrega un bloque `[WALKER] Workflow: …` al último
  mensaje del usuario, una vez por mensaje. No bloquea nada.
- **Trazas**: `session_start`, `agent_message`, `workflow_state_injected`,
  `workflow_suggestion_ignored` (si después de inyectar no hubo
  `submit`/`transition`), `session_end`. `trace_push` conserva `session_id` y
  `agent_id` en `metadata`.
- **Guards locales** (ya existían): bloquea `edit`/`write`/`read` sobre
  `.cognitive/`, `python3 -c` inline y bash destructivo sobre `.cognitive/`.

Escape hatch: `WALKER_WORKFLOW_INJECT=0` desactiva binding e inyección.

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
