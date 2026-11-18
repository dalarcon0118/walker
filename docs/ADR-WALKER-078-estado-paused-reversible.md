# ADR-WALKER-078 — Estado `paused` reversible

- **Estado:** aceptado
- **Fecha:** 2026-11-18
- **Relacionado:** ADR-WALKER-077 (sprint activo por sesión)

## Contexto

ADR-WALKER-077 rechazó demover el sprint anterior a `reviewing` al crear uno
nuevo porque el flujo de estados era lineal y unidireccional: `active →
reviewing` se permitía, pero `reviewing → active` no. Esa decisión dejó sin
respuesta un caso real: **pausar trabajo sin cerrarlo** (cambio de objetivo,
trabajo en paralelo, espera de una dependencia).

Sin un estado reversible, las únicas opciones eran dejar el sprint `active`
(aunque no se estuviera trabajando en él) o cerrarlo (destruyendo la intención
de reanudarlo). Ambas son mentiras sobre el estado del trabajo.

Además se detectó que `spec_service.py` definía una **copia local** del flujo con
un typo (`SPRINT_STATUS_FLOW`, sin G) que `can_transition` usaba en lugar del
SSOT de `values.py`: cualquier cambio en el dominio era invisible para la
validación.

## Decisión

1. **Agregar `paused`** al flujo: `planning → active → paused → reviewing →
   closed`.
2. **La transición se decide con un grafo explícito** (`SPRING_TRANSITIONS`),
   no con el índice de la lista. El orden de `SPRING_STATUS_FLOW` queda solo
   como enumeración para validación y presentación.
3. **Transiciones válidas:**
   - `planning → active | reviewing | closed`
   - `active → paused | reviewing | closed`
   - `paused → active | reviewing | closed`  ← **reversible**
   - `reviewing → closed`
   - `closed → (terminal)`
   - mismo estado = no-op exitoso (idempotencia)
4. **`transition_spring` devuelve el motivo del rechazo** (`{ok: false, error,
   from, to}`) en vez de ignorarlo. `set_spring_status` se conserva como
   compatibilidad (aplica si es válido, calla si no).
5. **SSOT único:** se elimina la copia local con typo y `can_transition` importa
   `SPRING_STATUS_FLOW` y `SPRING_TRANSITIONS` de `values.py`.
6. **Herramienta `sprint_status`** para operar el ciclo: acepta `spring_id` o
   `session_id` (resuelve el sprint de la sesión).

## Invariantes

- **INV-1:** todo estado pertenece a `SPRING_STATUS_FLOW`.
- **INV-2:** `closed` es terminal; ninguna transición sale de él.
- **INV-3:** `paused` es el único estado con vuelta a `active`.
- **INV-4:** pausar no modifica specs, tareas ni archivos: es solo estado del
  sprint.

## Alternativas rechazadas

- **Usar `reviewing` como pausa.** Rechazada: es unidireccional en el dominio
  actual y no puede volver a `active`.
- **Un flag booleano `suspended` junto a `status`.** Rechazada: dos fuentes de
  verdad para el mismo hecho; toda consulta tendría que combinar ambos.
- **Cerrar y reabrir con un sprint nuevo.** Rechazada: pierde continuidad y
  duplica el trabajo en el grafo.

## Consecuencias

- Se puede pausar y reanudar sin cerrar; el estado deja de mentir.
- ADR-WALKER-077 sigue vigente: varios sprints activos por proyecto son
  legítimos si pertenecen a sesiones distintas. `paused` no cambia esa regla ni
  introduce pausas automáticas.
- Los sprints existentes no se migran: `paused` es un estado nuevo, no una
  reinterpretación de los anteriores.
