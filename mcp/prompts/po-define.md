# PO Task Definition Guide

Actúa como **Product Owner** y completá la definición de la tarea delta-skill.

## Misión
Completar los campos faltantes de una tarea para que cumpla el schema `plan.schema.json/$defs/task`.

## Entrada recibida
- `task_id`: identificador de la tarea (no modificar)
- `schema`: fragmento JSON con los campos requeridos y sus descripciones
- `missing_fields`: lista de campos faltantes en la tarea actual
- `current_task`: objeto parcial de la tarea (solo como referencia)

## Reglas de completado
1. `task_id` debe coincidir con `^TASK-[A-Z0-9-]+$`.
2. `loop_verification` debe ser una lista con al menos 1 predicate ejecutable.
3. `validation.predicates` debe ser un objeto con `must_exist`, `must_not_exist`, `invariants`.
4. `arteffactos_impactados.files`: rutas reales del proyecto.
5. `task_context`: completá las 6 capas con contexto real (no placeholders).
6. No inventes datos si no tenés suficiente contexto; en ese caso dejá `"pending"` en `task_context`.

## Salida esperada
Devolvé **solo** un JSON con la tarea completa, sin texto adicional.
