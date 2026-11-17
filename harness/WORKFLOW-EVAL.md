# Workflow eval (plantillas del workflow, no prompts directos)

El eval normal corre con prompts directos por tarea y `--no-mcp`, así que las
plantillas del workflow (`implement/work`, `design/design-gather`, …) y el
contexto inyectado del CKG nunca se ejercitan. Para medir eso hace falta un
**modo workflow**.

## Schema de una tarea workflow

```json
{
  "id": "implement-workflow",
  "mode": "workflow",
  "base_commit": "<commit con el bug>",
  "test_from": "HEAD",
  "test_files": ["tests/test_x.py"],
  "workflow_prompt": "Proyecto walker: {project_id}. Corre `walker {project_id}/status`, "
                     "lee el sub-estado actual y ejecuta el paso que te indica. "
                     "No leas archivos antes de consultar al CKG. Cuando termines, "
                     "haz `walker {project_id}/submit` con el resultado y deja el "
                     "codigo implementado.",
  "test": "tests",
  "sanitize": true
}
```

Campos nuevos vs. tarea directa:
- `mode`: `"workflow"` (default: `"direct"`).
- `workflow_prompt`: prompt inicial. `{project_id}` se sustituye por el id del
  sandbox registrado en walker (`<basename del worktree>`).
- El agente corre **con MCP habilitado** (necesita hablar con walker).
- El runner registra el sandbox como proyecto (`prepare`) antes de lanzarlo, así
  el agente puede hacer `walker <id>/status`.

## Flujo del runner en modo workflow

1. `materialize` el sandbox (igual que directo).
2. `inject_tests` (igual).
3. `prepare(path=<sandbox>)` vía WalkerClient → obtiene `project_id`.
4. `git init` + commit `base` (el `.cognitive/` del prepare queda en el base).
5. Lanza el agente con MCP habilitado y el prompt sustituido.
6. Tras el agente: `pytest` (grader) y, si `--walker-gate`, `verify_predicates`.

## Limitaciones conocidas

- El walker-gate usa `project_root=sandbox` y puede disparar un CPG build del
  sandbox (≈2 min). Para evitarlo, omitir `--walker-gate` en workflow tasks.
- El eval no captura el `git diff` producido por el agente dentro del workflow
  (el agente hace sus propios commits o no). El grader asume que los cambios
  están en el working tree al terminar.
- Sin `--no-mcp` los índices del walker apuntan al repo real, por eso el
  runner registra el sandbox como proyecto nuevo: así el agente opera sobre su
  propio índice, no sobre `backend-microservices`.

## Por qué no mide comportamiento today

Las tareas directas usan prompts fijos, así que el agente nunca pasa por
`implement/work` y el contexto CKG inyectado no se ejercita. Una tarea
workflow es la única forma de medirlo, y además k≥3 por la varianza medida
(0,71x–2,20x en tokens por tarea).
