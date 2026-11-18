# ADR-WALKER-079 — Tarea en sprint pausado: rechazo explícito

- **Estado:** aceptado
- **Fecha:** 2026-11-18
- **Relacionado:** ADR-WALKER-078 (`paused` reversible)

## Contexto

Con ADR-WALKER-078 un sprint puede quedar `paused`. Pero `implement_task`
adjunta la spec al sprint de la sesión sin mirar su estado: una tarea nueva
podía aterrizar en un sprint que el propio sistema declara en pausa. El estado
mentía en la dirección contraria: decía "pausado" y seguía acumulando trabajo.

Las opciones eran reanudar automáticamente al declarar, o rechazar con una
instrucción. Reanudar implícitamente convierte `paused` en "nadie declaró nada
desde la última pausa": una garantía débil y, sobre todo, un cambio de estado
silencioso que el usuario no pidió.

## Decisión

1. **Por defecto, `implement_task` RECHAZA la tarea** si el sprint elegido está
   `paused`, con un mensaje accionable que incluye el `spring_id` y las dos
   salidas: reanudar con `sprint_status` o repetir con `resume: true`.
2. **`resume: true` es el opt-in explícito** del llamador: reanuda el sprint
   (`paused → active`) y continúa con la declaración.
3. **El rechazo es del handler, no de la spec**: no se persiste nada antes de
   validar el estado; el delta no queda con una tarea en un sprint pausado.
4. **`session_bind` avisa** cuando el sprint de la sesión está pausado y ofrece
   el comando para reanudarlo (antes de que el agente intente declarar).

## Invariantes

- **INV-1:** un sprint pausado no recibe tareas nuevas sin una acción explícita.
- **INV-2:** reanudar es siempre explícito (`sprint_status` o `resume: true`);
  nunca un efecto colateral de declarar.
- **INV-3:** el rechazo nombra el sprint y las dos salidas; no requiere leer
  código ni adivinar.
- **INV-4:** el estado del sprint y la presencia de tareas no se contradicen.

## Alternativas rechazadas

- **Auto-resume al declarar.** Rechazada: cambio de estado silencioso y
  `paused` pierde su significado operativo.
- **Permitir la tarea y dejar el sprint pausado.** Rechazada: contradice el
  estado y hace que "pausado" no signifique nada.
- **Rechazar sin ofrecer `resume`.** Rechazada: obliga a dos llamadas siempre,
  incluso cuando el llamador ya sabe que quiere reanudar.

## Consecuencias

- Una tarea declarada en un sprint pausado cuesta, por defecto, un round trip
  (rechazo → reanudar → reintentar). Es el mismo patrón que los otros gates y
  queda auditado en el trace.
- El plugin y el agente tienen dos caminos legítimos: reanudar primero o
  declarar con `resume: true`.
- Los sprints pausados siguen siendo visibles en `session_list` /
  `session_context` con su estado, sin ambigüedad.
