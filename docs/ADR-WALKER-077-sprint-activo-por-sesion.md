# ADR-WALKER-077 — Sprint activo por sesión (delta-sprint)

- **Estado:** aceptado
- **Fecha:** 2026-11-18
- **Contexto:** continuidad sesión → sprint (`session_bind`), registro de sesiones
  (`TYPE_SESSION`) y sprints creados por sesión (`SPR-S<hash>`).

## Contexto

La sesión es la unidad de trabajo del **agente**; el sprint (`Spring`) la unidad
de trabajo del **proyecto**. Con el protocolo de continuidad, cada chat crea o
reutiliza su propio delta-sprint y las tareas se adjuntan a él por `session_id`.

Eso produce, naturalmente, **varios sprints `active` en el mismo proyecto**: uno
por sesión viva. La regla previa del repositorio ("más de dos sprints formales
activos = anomalía") describía el modelo anterior, donde el proyecto tenía un
único sprint corriente. Aplicada hoy, marca como anomalía el estado normal.

## Decisión

1. **`active` es por sesión.** Un sprint está activo para la sesión que lo posee
   (`spring.session_id` o `spring.context.session_ids`). Varios sprints activos
   en un proyecto son legítimos si cada uno se atribuye a una sesión distinta.
2. **No se demueve el sprint anterior al crear uno nuevo.** El flujo de estados
   (`planning → active → reviewing → closed`) es **unidireccional**
   (`SprintStatusUpdater.can_transition` rechaza retrocesos), así que
   `active → reviewing` sería una puerta de una sola dirección: el sprint
   anterior no podría volver a activarse. Demover no es una operación segura.
3. **La anomalía real es la falta de atribución.** El repositorio advierte solo
   cuando hay sprints activos **sin** `session_id` o con `session_id`
   **duplicado**: en ese caso no se puede decidir a qué sesión pertenece cada
   uno, que es el estado que produce el "sprint hijacking".
4. **`get_active_spring()` queda como selector por defecto del proyecto**
   (formal sobre informal, más reciente primero). Ningún camino que conozca la
   sesión debe usarlo: usa la pertenencia por sesión. Es un default para
   consultas sin sesión (CLI humana, `sprint_planning`).
5. **`session_bind new` es idempotente por sesión**: si la sesión ya posee un
   sprint, no crea otro.

## Invariantes

- **INV-1:** una tarea pertenece a exactamente un sprint (spec → spring).
- **INV-2:** una sesión pertenece a lo sumo a un sprint.
- **INV-3:** todo sprint `active` es atribuible a una sesión, o el proyecto tiene
  un solo activo (caso sin sesiones).
- **INV-4:** crear trabajo nuevo no destruye trabajo en curso (no hay democión
  implícita ni cierre implícito).

## Alternativas rechazadas

- **Un solo `active` por proyecto, demoviendo el anterior a `reviewing`.**
  Rechazada: el flujo es unidireccional y `reviewing` no vuelve a `active`.
- **Agregar un estado `paused` reversible.** Correcta en semántica, pero cambia
  el flujo de estados del dominio y todos sus consumidores; queda como trabajo
  futuro si aparece la necesidad real de pausar sin cerrar.
- **Cerrar el sprint anterior al crear uno nuevo.** Rechazada: cierra trabajo en
  curso sin decisión del usuario (viola INV-4).

## Consecuencias

- El aviso de ">2 activos" deja de dispararse por un estado legítimo; se
  reemplaza por el aviso de atribución.
- Las consultas de proyecto (`session_list`, `get_active_spring`) siguen siendo
  deterministas, pero la fuente de verdad por sesión es la pertenencia.
- La jerarquía de sesiones (`parent_session_id`) y el sprint activo son ejes
  distintos: una sesión hija puede compartir el sprint del padre
  (`mode=continue`) o crear el suyo (`mode=new`).
