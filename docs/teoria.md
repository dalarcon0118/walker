# 🧭 GPS Cognitivo — Teoría del modelo delta

> Basado en ADR-006: El Proyecto como Modelo de Transformación

---

## 1. La ecuación fundamental

```
G_objetivo = G_actual + Δ
```

| Símbolo | Significado |
|---------|-------------|
| **G_objetivo** | El estado deseado: lo que queremos lograr |
| **G_actual** | El estado actual: lo que existe hoy |
| **+** | Aplicar la transformación (no es suma aritmética, es composición) |
| **Δ** | El conjunto de cambios necesarios para transformar G_actual en G_objetivo |

## 2. Descomposición de Δ

```
Δ = [tarea₁, tarea₂, ..., tareaₙ]

Cada tarea = subdelta {
  qué:  objetivo (el propósito de la tarea)
  cómo: impacted_artifacts (los archivos, APIs, datos que la tarea toca)
  validación: verificación de que el subdelta se completó
}
```

Cada **tarea** es un **subdelta** con la misma structure que el delta raíz:
transforma un subconjunto del sistema para acercarlo a G_objetivo. Cuando todas
las tareas están completas y verificadas, Δ está completo y G_objetivo se alcanzó.

## 3. Analogía fundacional

En relatividad general, Einstein describió la gravedad como **geometría del espacio-tiempo**.
En ingeniería de software, el mismo principio aplica:

> **El trabajo de un agente no es escribir código; es reducir la diferencia entre el modelo
> actual del sistema y el modelo deseado. El código es solo una manifestación física de ese cambio.**

## 4. Mapeo de tipos de tarea a Δ

| Tipo | Efecto en Δ |
|------|-------------|
| **Feature** | Añade nodos y relaciones al grafo |
| **Bugfix** | Corrige relaciones o invariantes rotas |
| **Refactor** | Preserva behavior pero reorganiza el grafo |
| **Migración** | Sustituye regiones completas del grafo |
| **Optimización** | Modifica propiedades sin alterar contratos |

## 5. Árbol completo del modelo de datos

```
G_objetivo = G_actual + Δ
                │
                ├── G_objetivo (estado deseado)
                │     ├── description    — qué queremos lograr
                │     ├── predicates     — must_exist / must_not_exist / invariants
                │     └── signatures     — firmas de funciones requeridas
                │
                ├── G_actual (estado actual)
                │     ├── existing_nodes — funciones, clases, módulos que existen
                │     ├── dependencies   — dependencias entre módulos
                │     └── total_distance — qué tan lejos estamos del G_objetivo
                │
                └── Δ = [sprint₁, sprint₂, ..., sprintₙ]  — DESCOMPOSICIÓN en deltas intermedios
                      │
                      └── cada sprint (delta intermedio) = subdelta
                            ├── sprint_id       — identificador único
                            ├── goal            — estado intermedio que representa (su sprint goal)
                            ├── predicates      — subconjunto de predicates de G_objetivo (criterio de completitud)
                            ├── status          — pending | in_progress | completed
                            │
                            └── Δᵢ = [tareas]   — cada tarea = subdelta
                                  ├── task_id          — identificador único
                                  ├── type             — feature | bugfix | refactor
                                  ├── state            — pending | in_progress | completed
                                  │
                                  ├── 📍 QUÉ
                                  │     └── objective  — qué debe lograr esta tarea
                                  │
                                  ├── 📦 CÓMO (artefactos impactados)
                                  │     ├── files      — archivos que lee/escribe
                                  │     ├── apis       — APIs que consume/ex pone
                                  │     ├── data       — datos que analiza/genera
                                  │     ├── config     — configuraciones que modifica
                                  │     ├── tests      — tests que genera
                                  │     └── docs       — documentación que produce
                                  │
                                  ├── ⚠️ RIESGOS
                                  │     ├── curvature  — impacto arquitectónico
                                  │     ├── pre_churn  — signos de abandono
                                  │     └── trust      — calidad de commits
                                  │
                                  ├── 💡 DECISIONES
                                  │     ├── chosen     — opción elegida
                                  │     └── rationale  — por qué
                                  │
                                  ├── 🔄 REFINAMIENTO
                                  │     ├── attempts   — iteraciones
                                  │     └── dead_ends  — caminos descartados
                                  │
                                  └── ✅ VALIDACIÓN
                                        ├── predicates — qué predicates satisface
                                        └── verified   — verificado? timestamp?

REGLA TEMPORAL DE ACTUALIZACIÓN:
  submit (dentro de Δᵢ)        → progreso LOCAL del sprintᵢ (NO mueve G_actual)
  completar sprintᵢ (predicates OK) → G_actual += Δᵢ   (aplicar ecuación, recalcular bloque)
  todos los sprints completos   → G_actual = G_objetivo, Δ = ∅

Descomposición: la SUMA de los goals de todos los sprints = estado deseado.
  ⋃ predicates(sprintᵢ) = predicates(G_objetivo), con predicatesᵢ ∩ predicatesⱼ = ∅ (i≠j).
```

## 6. Componentes del modelo

| Componente | Rol |
|------------|-----|
| **Proyecto** | El aggregate root. Agrupa G_objetivo, G_actual, Δ, trail. |
| **Goal (G_objetivo)** | Define el alcance como predicados verificables. |
| **G_actual** | Estado actual del sistema (funciones, clases, dependencias que existen). |
| **Sprint (delta intermedio)** | Descompone Δ en estados intermedios. Su goal + predicates son un subconjunto de G_objetivo; se completa al satisfacer sus predicates. |
| **Tarea (subdelta)** | Unidad atómica de transformación dentro de un sprint. Tiene qué + cómo + validación. |
| **Artefactos impactados** | Todo lo que la tarea toca: archivos, APIs, datos, config, tests. |
| **Decisiones** | Alternativas evaluadas y rationale de la elegida. |
| **Riesgos** | Curvatura arquitectónica, pre-churn, commit trust. |
| **Validación** | Predicados que la tarea satisface + verificación. |
| **Refinamiento** | Iteraciones: attempts, dead-ends. |

> **Regla temporal (ADR-WALKER-072):** el submit produce progreso *local* dentro del sprint en
> curso; G_actual solo se actualiza al **completar un sprint** (`G_actual += Δᵢ`). La ecuación
> `G_objetivo = G_actual + Δ` se evalúa al cerrar cada sprint. Ver modelo completo en ADR-WALKER-072.