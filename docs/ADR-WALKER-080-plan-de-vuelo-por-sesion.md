# ADR-WALKER-080 — Plan de vuelo por sesión

- **Estado:** aceptado
- **Fecha:** 2026-11-18
- **Relacionado:** ADR-WALKER-077 (activo por sesión), diagnóstico de foco

## Contexto

El control de vuelo guiaba con el prompt del nodo del workflow: hasta 1.200
caracteres de "Sprint Monitoring" inyectados en cada llamada. El agente los
ignoró en todas las sesiones medidas (`followed: false`, 5–8 inyecciones), y no
es casual: el bloque habla del proyecto (nodo, DAG, fases), mientras la atención
del agente está en una tarea y unos archivos. Sin destino verificable ni próximo
paso, el "GPS" solo puede decir en qué nodo está el proyecto.

## Decisión

Persistir un **plan de vuelo por sesión** en el registro (`Session.flight_plan`),
con esta forma:

```json
{
  "goal": "destino de la sesión",
  "acceptance": ["criterios verificables"],
  "task": "tarea actual",
  "scope": ["archivos en alcance"],
  "next": "próximo paso concreto",
  "updated_at": "ISO-8601"
}
```

- **Alta automática** en `session_bind mode=new`: `goal` = pedido del usuario;
  `acceptance` derivada del goal con la heurística existente (`derive`); si no
  hay criterios, `next` pide definirlos en lugar de inventarlos.
- **Actualización automática** en `implement_task`: `task` y `scope` pasan a ser
  los declarados, `next` = "implementar y verificar".
- **`session_plan`** (tool): `mode=get` devuelve el plan (vacío si la sesión
  existe sin plan), `mode=set` hace merge de campos.
- El plan vive en el registro de sesiones, no en memoria del plugin: sobrevive
  recargas y reinicios.

## Invariantes

- **INV-1:** el plan pertenece a una sesión registrada; sin sesión no hay plan.
- **INV-2:** `goal` es el destino; `acceptance` es lo que permite verificarlo.
  Si está vacío, el plan lo declara en `next` (no se inventan criterios).
- **INV-3:** `task`/`scope` reflejan la última tarea declarada, no una
  heurística de archivos abiertos.
- **INV-4:** `set` es merge: actualizar la tarea no borra el goal.

## Consecuencias

- El plugin puede renderizar un bloque **corto y estable** (destino, tarea,
  próximo paso) en lugar del template del nodo, y solo cuando cambia.
- Es posible medir foco con datos reales: turnos que referencian el goal,
  archivos tocados dentro de `scope`, cierres con `acceptance` verificada.
- El plan es visible para el usuario (vía `session_context` y el registro), que
  es requisito para que un GPS sirva.
- Los planes son texto; la verificación de `acceptance` sigue dependiendo del
  gate de cierre (hoy débil: cualquier `pytest` cuenta).
