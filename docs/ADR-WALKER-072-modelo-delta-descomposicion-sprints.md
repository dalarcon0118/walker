# ADR-WALKER-072 — Modelo delta refinado: descomposición de G_objetivo en sprints + regla de actualización temporal de G_actual

## 0. Contexto y objetivo

La teoría del modelo delta (`docs/teoria.md`) define:

```
G_objetivo = G_actual + Δ
```

donde `Δ = [tarea₁, ..., tareaₙ]` y cada tarea es un **subdelta**. Esta ecuación es correcta
pero **incompleta** para el propósito real de Walker por dos razones, detectadas al contrastar
la teoría contra la implementación (ver análisis crítico en `project.md`):

1. **No hay descomposición intermedia del estado deseado.** `Δ` se modela como una lista
   plana de tareas. En la práctica el objetivo del proyecto (G_objetivo) es demasiado grande
   para entregarse de una vez: se descompone en **varios estados intermedios** (en Scrum, cada
   uno es un **sprint**), y la **suma de los goals de todos los sprints es el estado deseado**.
   Walker necesita esta jerarquía `G_objetivo → sprints → tareas (subdeltas)` para poder
   razonar y medir progreso a escala.

2. **No hay regla temporal de actualización de G_actual.** La teoría no explica **cuándo**
   se actualiza G_actual. El análisis de la implementación reveló el error conceptual: cada
   `submit` **no** es un incremento del estado deseado. El submit ocurre *dentro* de un delta
   (un sprint en curso); G_actual solo avanza (aplica la fórmula) **cuando se completa un
   delta/sprint**, no en cada submit.

**Analogía fundacional (carrera de 100 metros):**
> Imagina que vas a correr 100 m. La pista tiene indicadores cada 10 m.
> - Estado deseado (G_objetivo): llegar a los 100 m con la mayor velocidad posible.
> - Estado actual (G_actual): arranca en 0.
> - Δ total descompuesto: 10 deltas de 10 m cada uno (10 sprints).
> - El corredor arranca, pasa el primer indicador, luego el segundo...
> - **Cada vez que pasa por un indicador de 10 m, recalcula mentalmente (compara) su estado
>   actual vs. el deseado.**
> - El incremento de "estado actual" ocurre **al completar cada segmento de 10 m**, no en
>   cada zancada (no en cada submit).

Este ADR **refina el modelo del dominio** (domain-first, SSOT) para formalizar las dos
piezas faltantes: la **descomposición jerárquica** y la **regla temporal de actualización**.
No es un ADR de implementación: define el modelo conceptual que luego guiará el port
(engine fast_mcp, delta_tracking, `submit`, etc.).

## 1. Diagnóstico (por qué el modelo actual falla)

### 1.1 La descomposición del estado deseado NO existe como capa propia

En `teoria.md` §5, el árbol es:

```
G_objetivo = G_actual + Δ
    ├── G_objetivo (predicates, signatures)
    ├── G_actual (existing_nodes, dependencies, total_distance)
    └── Δ = [tareas]  ← lista plana de subdeltas
```

No hay una capa intermedia entre `G_objetivo` y las `tareas`. El sprint (que sí existe en
`docs/delta-spring.md` como entidad con goal + tareas + predicates) **no está conectado** a
la ecuación fundamental ni a la regla de actualización de G_actual. El `spring` y el `delta`
se tratan como dominios desconectados.

### 1.2 No hay regla de cuándo se actualiza G_actual (error conceptual)

El análisis de la implementación (`project.md`) mostró que el port fast_mcp trata cada
`submit` como si avanzara el estado global (`task_ok=True` hardcodeado, revision bump),
**sin relación con el objetivo del proyecto ni con el sprint**. La teoría no distingue:

- **Movimiento local (dentro de un delta):** progreso del sprint en curso, medido por
  subtareas/submit. **No** recalcula la fórmula global.
- **Movimiento global (al completar un delta):** cuando un sprint se completa (sus predicates
  satisfechos), G_actual recibe ese bloque: `G_actual += Δ_spring` y el delta restante pasa a
  ser `G_objetivo - G_actual`.

Sin esta distinción, "el submit acerca al objetivo" es un juicio **falso**: un submit dentro
de un sprint que no está completo no mueve G_actual.

## 2. El modelo refinado (dominio SSOT)

### 2.1 La ecuación fundamental extendida

```
G_objetivo = G_actual + Δ
Δ = [spring₁, spring₂, ..., springₘ]          ← DESCOMPOSICIÓN en deltas intermedios
springᵢ = { goalᵢ, predicatesᵢ, Δᵢ = [tarea₁..tareaₖ] }
  donde: predicatesᵢ ⊂ predicates(G_objetivo)  y  ⋃ᵢ predicatesᵢ = predicates(G_objetivo)
```

Regla **fundamental**: la suma de los goals de todos los sprints = estado deseado.

```
G_objetivo = G_actual + Δ
regla temporal:
    submit (dentro de Δᵢ)          → progreso LOCAL del springᵢ (no mueve G_actual)
    completar springᵢ (predicatesᵢ OK) → G_actual += Δᵢ   (recalcular bloque)
    cuando todos los springs completos → G_actual = G_objetivo (Δ = ∅)
```

### 2.2 Analogía carrera de 100 m → modelo

| Carrera de 100 m | Modelo delta |
|------------------|--------------|
| Pista de 100 m | G_objetivo (estado deseado) |
| Indicador cada 10 m | Descomposición en 10 segmentos (sprints) |
| Zancada individual | Submit (no mueve el marcador global) |
| **Pasar un indicador de 10 m** | **Completar un spring (predicatesᵢ vendrían)** → G_actual += Δᵢ |
| Recalcular al pasar el indicador | Recalcular `G_objetivo - G_actual` = delta restante |
| Llegar a 100 m | Todos los springs completos → G_actual = G_objetivo |

### 2.3 Descomposición del estado deseado (Problema 1)

- **G_objetivo** se define por **predicates verificables** (`must_exist` / `must_not_exist` /
  `invariants`), como ya establece `teoria.md`.
- **Δ se descompone en sprints**, y cada sprint toma un **subconjunto disjunto y exhaustivo**
  de los predicates de G_objetivo: `springᵢ.predicates`. Cuando `springᵢ.predicates` están
  satisfechos, ese sprint está completo.
- Cada sprint tiene su **goalᵢ** (descripción del estado intermedio que representa) y sus
  **tareas (subdeltas)** `{qué, cómo, validación}`.
- **Criterio de completitud del spring (decisión de diseño):** el sprint se considera completo
  cuando **se satisfacen los predicates de su spec** (`springᵢ.predicates`), no por el simple
  recuento de tareas cerradas. (Decisión del usuario.)

### 2.4 Regla temporal de actualización (Problema 2)

- **Submit = avance parcial del delta/sprint en curso.** El submit aporta evidencia y progreso
  *local* al sprint en curso. El agente (como el corredor en el indicador de 10 m) **recalcula
  mentalmente** el progreso local, pero **no** ejecuta la fórmula global.
- **Completar un sprint** → aplicar `G_actual += Δᵢ`. Solo aquí se actualiza el estado actual
  según la ecuación global.
- **Delta restante** tras cada sprint completado: `G_actual_restante = G_objetivo - G_actual`
  (los springs no entregados).
- Al terminar todos los sprints se alcanza el estado deseado.

### 2.5 Consecuencias para el modelo de datos

Se extiende el árbol de `teoria.md` §5 (ver §3 abajo, edición propuesta) con:

```
Δ = [spring₁ .. springₙ]
    ├── cada spring:
    │     ├── goalᵢ          — estado intermedio que representa
    │     ├── predicatesᵢ    — subconjunto de predicates de G_objetivo (criterio de completitud)
    │     ├── status         — pending | in_progress | completed
    │     └── Δᵢ = [tareas]  — subdeltas {qué, cómo, validación, riesgos, decisiones, refinamiento}
    └── actualización:
          completar springᵢ → G_actual += Δᵢ
          resto = G_objetivo - G_actual
```

## 3. Edición propuesta de `docs/teoria.md` §5

Se añade la capa de sprints y la regla de actualización al árbol del modelo de datos
(del §5). Cambio propuesto:

```
G_objetivo = G_actual + Δ
    ├── G_objetivo (estado deseado)
    │     ├── description    — qué queremos lograr
    │     ├── predicates     — must_exist / must_not_exist / invariants  (⋃ = se reparten entre sprints)
    │     └── signatures     — firmas de funciones requeridas
    ├── G_actual (estado actual)
    │     ├── existing_nodes
    │     ├── dependencies
    │     └── total_distance — qué tan lejos (G_objetivo − G_actual)
    └── Δ = [Δ₁, Δ₂, ..., Δₙ]  = [sprint₁, sprint₂, ..., sprintₙ]
          └── cada sprint (delta intermedio):
                ├── spring_id
                ├── goal          — estado intermedio (su goal, en Scrum)
                ├── predicates    — subconjunto de predicates de G_objetivo (criterio de completitud)
                ├── status        — pending | in_progress | completed
                └── Δᵢ = [tareas] — cada tarea = subdelta {qué, cómo, validación, riesgos, decisiones, refinamiento}
    regla temporal:
        submit (dentro de Δᵢ)    → progreso local del sprintᵢ (no mueve G_actual)
        completar sprintᵢ        → G_actual += Δᵢ
        todos los sprints hechos  → G_actual = G_objetivo (Δ = ∅)
```

> Respecto a la nomenclatura: el código/docs existentes usan **sprint** en el workflow y
> **spring** en `delta-spring.md`. En este ADR se usa **sprint** (terminología Scrum) pero se
> registra el desajuste como pendiente de normalizar (ver §7 cross-refs / checklist 2º orden).

## 4. Reglas de diseño (non-negotiables)

- R-01: **G_objetivo se descompone en sprints** cuyos predicates son un subconjunto disjunto
  y exhaustivo de los predicates del objetivo: `∃ ⋃ᵢ predicatesᵢ = predicates(G_objetivo)`, con
  `predicatesᵢ ∩ predicatesⱼ = ∅` para `i≠j`.
- R-02: **El criterio de completitud de un sprint es la satisfacción de sus predicates** de
  spec, no el cierre administrativo de tareas. Una tarea cerrada sin verificación NO completa
  el sprint.
- R-03: **Un submit NO incrementa G_actual.** El submit es progreso *local* del sprint en
  curso. G_actual solo se actualiza al **completar un sprint** (`G_actual += Δᵢ`).
- R-04: El **delta restante** se computa como `G_objetivo − G_actual` tras cada sprint cerrado,
  y corresponde a los sprints no entregados.
- R-05: La ecuación `G_objetivo = G_actual + Δ` se evalúa al **completar cada sprint** (cuando
  se actualiza G_actual), no en cada submit.
- R-06: El modelo vive en `docs/teoria.md` §5 (dominio SSOT). La implementación (engine,
  delta_tracking, submit) consumirá este modelo y NO debe reconstruirlo ni divergir.

## 5. failure_scenarios

```json
[
  {
    "fallo": "Un sprint se marca como 'completed' cerrando tareas administrativamente sin verificar sus predicates, y G_actual avanza sin que el estado real del sistema cambie",
    "causa_raiz": "tratar el cierre de tareas como criterio de completitud del sprint (R-02 violada)",
    "mitigacion": "el criterio de completitud del sprint ES la satisfaccion de spring.predicates (verify_predicates); el close_sprint verifica predicates antes de permitir G_actual += Δᵢ",
    "deteccion": "test: sprint con predicates sin satisfacer no puede transicionar a completed ni actualizar G_actual"
  },
  {
    "fallo": "Cada submit se interpreta como incremento del estado deseado, inflando la percepcion de progreso global y reportando total_distance erroneo",
    "causa_raiz": "confundir progreso local (dentro del sprint) con movimiento global de G_actual (R-03 violada)",
    "mitigacion": "submit devuelve progreso del sprint en curso (local); solo el cierre del sprint recalcula G_actual y total_distance=G_objetivo-G_actual",
    "deteccion": "test: N submits dentro de un sprint inacabado no alteran G_actual/total_distance; un unico sprint completado si lo altera"
  },
  {
    "fallo": "La suma de los goals/predicates de los sprints no cubre el estado deseado (predicates duplicados o ausentes), de modo que aun cerrando todos los sprints G_actual != G_objetivo",
    "causa_raiz": "descomposicion no exhaustiva/no disjunta de predicates(G_objetivo) entre sprints (R-01 violada)",
    "mitigacion": "invariante de descomposicion: ⋃ predicatesᵢ == predicates(G_objetivo) y predicatesᵢ∩predicatesⱼ=∅; validacion en create_sprint/plan",
    "deteccion": "test: al planificar, la union de predicates de todos los sprints es exactamente el conjunto de predicates del objetivo"
  },
  {
    "fallo": "El modelo de teoria.md (lista plana de tareas) y el de delta-spring.md (sprint no conectado a la ecuacion) divergen, dejando el dominio sin un SSOT coherente",
    "causa_raiz": "falta conectar la capa de sprints con la ecuacion G_objetivo=G_actual+Δ",
    "mitigacion": "teoria.md §5 se extiende con la capa de sprints y la regla temporal (este ADR); delta-spring.md referenciara este modelo como SSOT",
    "deteccion": "revision de docs: teoria.md §5 contiene la capa de sprints; delta-spring.md no contradice R-01..R-05"
  },
  {
    "fallo": "Nomenclatura inconsistente 'sprint' (workflow) vs 'spring' (delta-spring.md) confunde la implementacion al mapear el modelo",
    "causa_raiz": "terminologia divergente entre artefactos",
    "mitigacion": "normalizar a 'sprint' (Scrum) en este ADR; registrar el desajuste de nomenclatura como pendiente y alinearlo al portar",
    "deteccion": "grep de nomenclatura en docs y fast_mcp al implementar la conexion delta<->workflow"
  }
]
```

## 6. Checklist de segundo orden

- **¿Qué módulos importan esto?** El engine fast_mcp (`features/workflow`), `delta_tracking`
  (sprints/specs), y los use-cases de `submit`/`transition` que hoy no conocen la descomposición.
- **¿Qué side effects dispara?** La actualización de G_actual al completar un sprint dispara el
  recomputo de `total_distance` (G_objetivo − G_actual) y la marca del sprint como completed.
- **¿Qué eventos de dominio publica?** Un evento `sprint_completed(spring_id, Δᵢ)` que
  desencadena `G_actual += Δᵢ`; consumido por el estado del proyecto (G_actual) y por métricas.
- **¿Qué tests dependen de este comportamiento?** Los tests de workflow E2E y de delta_tracking
  que validan avanzar de estado; hoy asumen que submit avanza el estado (habrá que corregir la
  expectativa a "submit avanza local, sprint completa global").
- **¿Qué cambia en MCP adapter, CLI, CKG, DeltaGraph, Trace?** El contrato de `submit` (retorna
  progreso local, no incremento global), `status` (expone `spring.predicates`, `total_distance`
  global vs. progreso local), DeltaGraph (persiste la relación G_objetivo→sprints→tareas), Trace
  (traza `sprint_completed`).

> Este ADR es **conceptual/domain-first**: define el modelo. Su materialización en código
> (port de verify_predicates por sprint, actualización de G_actual en close_sprint, etc.) es
> trabajo de fases posteriores y se rige por los ADRs de arquitectura ya aprobados (045, 062, 067).

## 7. Cross-references

- `docs/teoria.md` — teoría base del modelo delta (se extiende en §5 con este ADR).
- `docs/delta-spring.md` — entidad spring/sprint y su nodo de workflow (a alinear como SSOT).
- `project.md` — análisis crítico teoría vs. implementación que motivó este refinamiento
  (obs: "submit NO ejecuta la transformación", "no se mide distancia a G_objetivo").
- ADR-071 — migración a fast_mcp; el port del engine deberá reflejar este modelo.
- ADR-045/062/067 — composition root, puertos y dominio puro (el modelo vive en el dominio).
- `docs/domain-first.md` — estrategia domain-first (este ADR es SSOT de dominio).

## 8. Estado / aprobación

- En revisión. `failure_scenarios` presente (5 ≥ 3). Pendiente de aprobar por el usuario.
- Edición de `docs/teoria.md` §5: PENDIENTE de aplicación (parte del alcance de este ADR).
