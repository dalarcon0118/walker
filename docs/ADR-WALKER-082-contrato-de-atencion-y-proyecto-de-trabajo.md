# ADR-WALKER-082 — Contrato de atención del bloque de plan y proyecto de trabajo

- **Estado:** aceptado
- **Fecha:** 2026-11-18
- **Relacionado:** ADR-WALKER-080 (plan de vuelo), ADR-WALKER-081 (alcance)

## Contexto

El plugin inyectaba el template del nodo del workflow (hasta 1.200 caracteres) en
cada llamada al modelo; el agente lo ignoró en todas las sesiones medidas. La
alternativa ingenua —embeber el `preflight` completo— habría sido peor: miles de
caracteres por turno. Además, una sesión puede correr en un proyecto y trabajar
sobre otro (`game_reset` editando `walker`), y el proyecto de trabajo se estaba
confundiendo con el de ubicación.

## Decisión

### Jerarquía de información (L0–L3)

| Capa | Contenido | Dónde | Presupuesto |
| :--- | :--- | :--- | :--- |
| L0 | goal, tarea, próximo paso | bloque del plan | ≤ 180 chars |
| L1 | blast radius, complejidad, tests | línea `Baseline` | ≤ 140 chars |
| L2 | preflight completo | tool `preflight` | bajo demanda |
| L3 | decisión del CKG | máximo 1 id en la línea | ≤ 60 chars |

- El bloque entero **no supera 400 caracteres**; la línea de baseline, 140.
- Solo se muestran los **dos archivos de mayor riesgo** (llamadores × complejidad);
  el resto se resume.
- Se inyecta **solo cuando cambia la firma del plan** (goal, tarea, alcance,
  próximo, criterios, baseline). La repetición es lo que entrena al modelo a
  ignorar el bloque.
- Si `preflight` falla o excede el timeout, no se inyecta baseline: el bloque cae
  al plan y la sesión sigue (fail-open).
- Se retira el bloque multilínea de preflight que se inyectaba aparte: dos
  baselines era exactamente el colapso que se quería evitar.

### Proyecto de trabajo vs. ubicación

- `rt.projectId` (ubicación, cwd) **no** es el proyecto de trabajo.
- Resolución: `plan.project` → archivos del `scope` que existan (resueltos con
  `project_for_path`) → cwd como último recurso.
- `project_for_path` resuelve por **prefijo**: se exige que el archivo exista
  antes de aceptar el proyecto, o el candidato de la sesión gana siempre.
- El proyecto de trabajo se usa en `preflight`, en el comando de cierre
  (`run_tests`) y en `session_bind`.
- Si la evidencia es ambigua (sesión nueva, sin plan, sin archivos), el agente
  **pregunta una vez** al usuario, con las opciones resueltas, y la respuesta se
  persiste en el plan/sprint. No se pregunta si el sprint o el plan ya lo dicen.

## Invariantes

- **INV-1:** un solo bloque de contexto por turno; sin duplicación.
- **INV-2:** el bloque no supera 400 caracteres y no repite si no cambió.
- **INV-3:** hechos del proyecto antes que instrucciones.
- **INV-4:** nunca se asume el proyecto de ubicación para archivos de otro repo.
- **INV-5:** un fallo de walker degrada el bloque, no rompe la sesión.

## Consecuencias

- El agente arranca con el riesgo real del archivo (llamadores, tests,
  complejidad) en lugar de una orden.
- El cierre propone el comando del proyecto correcto.
- El costo de atención es acotado y medible (`session_metrics`, chars del bloque).
- Limitaciones: los paths de `shell` no se resuelven; el baseline se calcula por
  archivo del `scope` y no expande directorios; la priorización por riesgo es una
  heurística.
