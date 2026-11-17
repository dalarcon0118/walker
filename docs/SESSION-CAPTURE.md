# Captura de sesión en walker

Cómo se une una sesión de agente con las cuatro memorias del proyecto.

## La clave: `session_id`

Una sesión se identifica con el id que OpenCode le asigna (`ses_...`). Cada capa
lo guarda en un lugar distinto:

| capa | dónde vive el id | quién lo escribe |
|---|---|---|
| TRACE | `event.metadata.session_id` | plugin (`trace_push`), tools |
| DELTA | `spec.task_context.session_id` | `implement_task` |
| CKG | `node.metadata.session_id` | `capture_knowledge`, `documenting_knowledge` |
| CPG | — (se alcanza por los archivos) | join vía `files` del trace/spec |

`session_context(session_id)` devuelve las cuatro vistas juntas y declara en
`empty_layers` cuál no tiene datos.

## De dónde sale cada dato

- **Tareas**: `implement_task` crea o actualiza la spec, con objetivo, archivos,
  criterios de aceptación y predicados derivados del pedido.
- **Hechos**: `capture_knowledge` (sub-estados `auto` `KNOWLEDGE_CAPTURE` y
  `KNOWLEDGE_UPDATE`) resume la sesión; `documenting_knowledge` guarda lo que el
  agente decide.
- **Archivos**: los declara la tarea y los agrega el trace.
- **CPG**: forma de los archivos tocados (símbolos, llamadas, llamadores).

## Trampas conocidas

- `save_nodes` descarta en silencio los nodos sin `label` (ahora lo deriva y
  reporta `skipped`).
- `session_id` en `source` no sirve: el join filtra por `metadata.session_id`.
- El plugin se carga al arrancar la sesión: cambios en el plugin no aplican a
  sesiones ya abiertas.
