---
description: Ata esta sesión a walker (proyecto + estado + baseline) sin crear tareas
agent: build
---

Atá esta sesión al proyecto en walker. Este comando **no crea tareas**: solo
resuelve el proyecto, lee el estado del workflow y toma la foto del baseline.
La tarea se declara cuando exista un pedido concreto.

1. Resolvé el proyecto por el directorio de trabajo:

   ```bash
   walker3 <proyecto>/project_for_path '{"path":"'"$PWD"'"}'
   ```

   Si no trae `project_id`, decilo y pará. No inventes un proyecto.

2. Leé el estado del workflow:

   ```bash
   walker3 <proyecto>/workflow_state '{}'
   ```

   Reportá `current_position`, `required_fields` y `task_id`.

3. Si hay `task_id`, traé su contexto; si no, no llames con un id inventado:

   ```bash
   walker3 <proyecto>/agent_context '{"task_id":"<task_id>"}'
   ```

4. Tomá la foto del baseline del proyecto:

   ```bash
   walker3 <proyecto>/snapshot '{"label":"bind"}'
   ```

Resumí en cuatro líneas: proyecto, posición del workflow, tarea en curso (o
«ninguna») y qué dimensiones del baseline quedaron disponibles. Si alguna capa
vino vacía o `unavailable`, decilo.

Cuando el usuario pida trabajo concreto, recién ahí:

```bash
walker3 <proyecto>/implement_task '{"task_id":"<slug>","session_id":"<ses_id>",
  "objective":"<pedido LITERAL del usuario>","files":["<archivo>"]}'
```

y revisá el `spec.validation` que devuelve.
