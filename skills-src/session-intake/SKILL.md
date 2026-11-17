---
name: session-intake
description: Ata la sesión al proyecto en walker antes de tocar código — resuelve proyecto, declara la tarea, toma el baseline y cierra con conocimiento. Usar al empezar cualquier implementación.
allowed-tools: Bash, Read
version: 1.0
priority: HIGH
tags: [intake, workflow, walker]
---

# Session intake — atar la sesión al proyecto

Sin esto, el trabajo de la sesión no queda en walker: no hay tarea declarada, no
hay baseline, no hay decisión registrada y después nadie puede reconstruir qué
pasó.

## Al empezar (antes de leer o editar)

1. Resolvé el proyecto por el directorio de trabajo:

   ```bash
   walker3 <proyecto>/project_for_path '{"path":"<cwd>"}'
   ```

   Si no está registrado, **no lo inventes**: preguntá al usuario.

2. Declará la tarea con el pedido REAL del usuario (no un resumen tuyo), y los
   archivos que esperás tocar:

   ```bash
   walker3 <proyecto>/implement_task '{"task_id":"<slug>","session_id":"<ses_id>",
     "objective":"<pedido literal>","files":["<archivo>"]}'
   ```

   `implement_task` deriva criterios de aceptación, precondiciones y predicados.
   Revisá el `spec.validation` que devuelve: si dice `ok: false`, mirá los
   `errors` antes de seguir.

3. Traé el contexto del ancla:

   ```bash
   walker3 <proyecto>/agent_context '{"task_id":"<slug>"}'
   ```

   Devuelve `anchor` (task/file/text), impacto del CPG, decisiones del CKG y la
   traza. Si `anchor.reason` dice que no encontró la tarea, no sigas como si
   hubiera contexto.

## Al terminar

4. Cerrá con lo que decidiste y por qué (una entrada por decisión):

   ```bash
   walker3 <proyecto>/documenting_knowledge '{"type":"decision",
     "statement":"<qué se decidió>","rationale":"<por qué>",
     "session_id":"<ses_id>","task_id":"<slug>"}'
   ```

5. Verificá que quedó unido:

   ```bash
   walker3 <proyecto>/session_context '{"session_id":"<ses_id>"}'
   ```

   `empty_layers` tiene que venir vacío. Si una capa está vacía, es un hueco real
   y hay que decirlo, no taparlo.

## Reglas

- No inventes criterios ni predicados: si no se pueden derivar, quedan en
  `unmapped` y se reporta.
- Un predicado `heuristic: true` detecta roturas cerca del cambio; no prueba el
  comportamiento nuevo.
- Si el proyecto no está registrado o el baseline falla, **decilo** y seguí con
  lo que haya; nunca simules que walker cubrió la tarea.
