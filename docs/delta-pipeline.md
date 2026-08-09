# 🔄 Delta Pipeline — Ejecución Detallada

> El pipeline completo de navegación GPS, paso a paso.
> Consta de 6 fases: 0→Proyecto, 1→G_objetivo, 2→G_actual, 3→Tareas, 4→Riesgo, 5→Score+Archivo.
>
> 📌 **Arquitectura**: El LLM **solo ejecuta `walker.py`**. Todo el pipeline
> detallado aquí se ejecuta **internamente** dentro de los scripts que `walker.py`
> invoca según el nodo del workflow (CREATE_SPRINT → `spring.py create`,
> ADVANCE_TASK → `task.py advance`, CLOSE_SPRINT → `spring.py close`).
>
> 📌 **Referencia**: Cada fase usa herramientas específicas definidas en
> [`tools.json`](tools.json) (ver [sección Tools en SKILL.md](SKILL.md#tools)).
> El `task.type` de cada tarea en `plan.json` determina qué tool ejecutar.

---

## API Pública (walker.py) vs Interna (spring.py / task.py)

| Lo que el LLM ejecuta | Nodo del workflow | Lo que ocurre internamente |
|---|---|---|
| Comando walker.py | Rol del nodo | Script interno delegado |
|--------|-------------|---|
| `walker.py transition sprint_created` | CREATE_SPRINT (manual) | `spring.py create ...` → FASE 0→1→2→3→4→5 (setup completo) |
| `walker.py transition proceed` | USER_GATE (manual) → ADVANCE_TASK (auto) | `task.py advance ... --act-as dev --confirm` → Enforcement → Score → TASK LOOP → Qlty → Confirm |
| `walker.py transition proceed` | REPORT_GATE (manual) → VERIFY (auto) | `task.py advance ... --act-as qa` → ADVANCE con Qlty obligatorio |
| `walker.py transition po_approved` | PO_REVIEW → IN_PROGRESS | `task.py advance ... --act-as po --human-confirm` → ADVANCE directo |
| `walker.py transition health_ok` | CLOSE_CHECK (auto) → CLOSE_SPRINT (auto) | `spring.py close` → CLOSURE: Verificación composicional + trail |
| `walker.py status` | Cualquier nodo | `spring.py list / status` + estado del walker |
| `walker.py verify` | VERIFY / CODE_REVIEW | `task.py qlty-check` → Qlty metrics + quality gates |

---

## Diagrama de flujo (ejecutado internamente por `walker.py` en el nodo CREATE_SPRINT)

```
walker.py transition sprint_created
  │
  └── walker.py invoca internamente:
      spring.py create <chain_dir> --project-id <id> --goal "..." --predicates '{}' --tasks '[]'
        │
        ├── FASE 0: DETECTAR PROYECTO
  │     ├── project_id: del argumento --project-id
  │     ├── project_type: por markers (pyproject.toml, package.json)
  │     └── domains: bounded contexts del proyecto (de project.json)
  │
  ├── FASE 1: DEFINIR G_objetivo (TargetSpec)
  │     ├── description: del argumento --goal
  │     ├── predicates: must_exist + must_not_exist + invariants
  │     └── signatures: firmas de funciones requeridas
  │
  ├── FASE 2: MAPEAR G_actual
  │     ├── Escanear archivos relevantes (grep/pytest sobre project_root)
  │     ├── Evaluar cada predicate contra el código real
  │     └── Calcular total_distance
  │
  ├── FASE 3: DESCOMPONER GOAL EN TAREAS
  │     ├── Por cada predicate insatisfecho → una tarea (o desde --tasks)
  │     ├── Asignar task.type según tipo de predicate
  │     └── Construir structure completa de cada tarea
  │
  ├── FASE 4: EVALUAR RIESGO POR TAREA
  │     ├── curvature_risk: impacto arquitectónico
  │     ├── pre_churn: signos de abandono
  │     └── commit_trust: calidad de commits
  │
  └── FASE 5: GENERAR ARCHIVOS + ENFORCEMENT
        ├── Construir plan.json + progress.json
        ├── Escribir .cognitive/<chain_dir>/plan.json
        ├── Escribir .cognitive/<chain_dir>/progress.json
        ├── Validar contra schemas (ENFORCEMENT)
        └── Si falla validación → eliminar archivos, exit 2
```

---

## FASE 0: Detectar proyecto

### INPUT
`--project-id` del argumento de `spring.py create` (invocado internamente por walker.py
en el nodo CREATE_SPRINT), o detectado del contexto del proyecto.

### PROCESO
0.1 Identificar `project_id`:
   - Del argumento `--project-id`
   - O resolver desde `ProjectRegistry.resolve_active_project()` (vía `projects.py`)
   - O nombre del directorio actual (basename)

0.2 Identificar `project_type`:
   - Buscar `pyproject.toml` → Python
   - Buscar `Cargo.toml` → Rust
   - Buscar `package.json` → Node/TypeScript
   - Buscar `cargo` o `npm` en structure → según corresponda

0.3 Identificar `domains` (bounded contexts):
   - Escanear directorios top-level en `projects/`, `src/`, `experimental/src/`
   - Cada directorio con archivos de código es un dominio candidato

0.4 Identificar `tags`:
   - Tecnologías detectadas: fastapi, flask, django, express, next, react
   - Patrones: hexagonal, DDD, MVC, clean architecture
   - ORM: sqlalchemy, prisma, diesel
   - Testing: pytest, jest, unittest

### OUTPUT
```json
{
  "project_id": "game_reset",
  "project_type": "backend",
  "domains": ["game", "auth", "billing"],
  "tags": ["python", "fastapi", "sqlalchemy", "hexagonal", "pytest"]
}
```

---

## FASE 1: Definir G_objetivo (TargetSpec)

### INPUT
`$goal` del argumento `--goal` de `spring.py create`.

### PROCESO
1.1 Parsear el goal en descripción estructurada.

1.2 Extraer predicates:

| Palabra clave en goal | Predicate generado |
|---|---|
| "implementar función X" | must_exist: "function X" |
| "crear clase X" | must_exist: "class X" |
| "eliminar Y" | must_not_exist: "Y" |
| "migrar de X a Y" | must_exist: "Y" + must_not_exist: "X" |
| "sin romper X" | invariant: "X" |
| "preservando X" | invariant: "X" |
| "refactorizar X" | invariant: "API público de X" |

1.3 Extraer signatures (firmas de funciones):
   - Si el goal menciona parámetros o tipos de retorno
   - Si hay convenciones en el proyecto existente

### OUTPUT
```json
{
  "description": "Implementar reseteo de juego con jackpot",
  "state": "defined",
  "predicates": {
    "must_exist": ["function reset_jackpot", "function calculate_jackpot", "class JackpotConfig"],
    "must_not_exist": ["function legacy_jackpot"],
    "invariants": ["reset_jackpot debe llamar a calculate_jackpot"]
  },
  "signatures": ["reset_jackpot(game_id: int) -> JackpotResult"]
}
```

---

## FASE 2: Mapear G_actual

### INPUT
TargetSpec + escaneo del código (vía grep/pytest internamente).

### PROCESO
2.1 Escanear archivos en los directorios del proyecto.

2.2 Para cada predicate `must_exist`:
   - Buscar si la función/clase existe en el código
   - Usar grep para buscar definiciones
   - Usar búsqueda semántica si está disponible

2.3 Para cada predicate `must_not_exist`:
   - Verificar que la función/clase NO exista

2.4 Para cada invariant:
   - Verificar que la regla se cumpla en el código actual

2.5 Calcular:
```
total_distance = sum(gaps de cada predicate) / count(predicates)
predicates_satisfied = count(predicates con gap=0) / count(predicates)
```

### OUTPUT
```json
{
  "existing_nodes": [
    {"type": "module", "name": "src/game", "file": "src/game/"},
    {"type": "function", "name": "calculate_score", "file": "src/game/score.py"}
  ],
  "dependencies": [
    {"from": "src/game/game.py", "to": "src/game/score.py", "type": "import"}
  ],
  "total_distance": 0.75,
  "predicates": [
    {"name": "function reset_jackpot", "type": "must_exist", "satisfied": false, "gap": 1.0},
    {"name": "function calculate_jackpot", "type": "must_exist", "satisfied": false, "gap": 1.0},
    {"name": "class JackpotConfig", "type": "must_exist", "satisfied": false, "gap": 1.0},
    {"name": "function legacy_jackpot", "type": "must_not_exist", "satisfied": true, "gap": 0.0}
  ]
}
```

---

## FASE 3: Descomponer goal en tareas

> 🔧 **Tool mapping**: El `type` asignado aquí determina qué tools de
> [`tools.json`](tools.json) se ejecutarán después (ver sección **Tools** en
> [SKILL.md](SKILL.md#tools)). Por ejemplo, tareas `refactor` activan
> Qlty quality gates dentro de `task.py advance`.

### INPUT
TargetSpec (Fase 1) + G_actual (Fase 2).

### PROCESO
3.1 Por cada `must_exist` con `satisfied=false` → crear tarea:
   - type: "feature"
   - objective: "Implementar {nombre}"
   - domain: inferido del contexto del predicate

3.2 Por cada `must_not_exist` con `satisfied=false` → crear tarea:
   - type: "bugfix" o "refactor"
   - objective: "Eliminar {nombre}"

3.3 Por cada invariant con `satisfied=false` → crear tarea:
   - type: "bugfix"
   - objective: "Corregir {invariant}"

3.4 Para cada tarea, determinar impacted_artifacts:
   - files: archivos nuevos (creación) o existentes (modificación)
   - tests: tests asociados

3.5 Asignar task_id secuencial: TASK-001, TASK-002, ...

3.6 Si se usó `--tasks '[...]'` en `spring.py create`, las tareas vienen pre-definidas.

### OUTPUT
```json
{
  "tasks": [
    {
      "task_id": "TASK-001",
      "type": "feature",
      "objective": "Crear clase JackpotConfig con atributos game_id, amount, currency",
      "domain": "game",
      "state": "pending",
      "result_state": "JackpotConfig creada en src/game/models.py",
      "impacted_artifacts": {
        "files": ["src/game/models.py"],
        "tests": ["tests/test_jackpot_config.py"]
      },
      "risks": {},
      "validation": {"predicates": ["JackpotConfig existe con atributos game_id, amount, currency"]},
      "analysis": "JackpotConfig es el modelo de datos que necesitan calculate_jackpot y reset_jackpot. Sin él, las otras funciones no pueden operar.",
      "refinement": {"attempts": 0, "dead_ends": []}
    }
  ]
}
```

---

## FASE 4: Evaluar riesgo por tarea

### INPUT
Tasks de Fase 3.

### PROCESO
4.1 Por cada tarea, calcular:

**curvature_risk** (impacto arquitectónico):
| Tipo de cambio | Riesgo |
|---|---|
| Archivo nuevo, sin dependencias | 0.05 |
| Archivo nuevo, depende de 1-2 módulos existentes | 0.08-0.12 |
| Modificar archivo existente, 0-2 dependencias | 0.10-0.20 |
| Modificar archivo existente, 3+ dependencias | 0.20-0.40 |
| Cambiar interfaz pública / API | 0.30-0.50 |

**pre_churn** (signos de abandono):
| Condición | Riesgo |
|---|---|
| Múltiples dead-ends en la sesión | +0.10 cada uno |
| Branches sin merge por más de 7 días | +0.05 cada uno |
| Sin commits en más de 3 días | +0.10 |
| Sesión actual con +20 tool calls sin progreso | +0.15 |

**commit_trust** (calidad de trabajo reciente):
| Condición | Riesgo |
|---|---|
| Commits frecuentes (cada <5 tool calls) | 0.05-0.10 |
| Commits espaciados (cada >20 tool calls) | 0.20-0.30 |
| Commits grandes (+10 archivos) | 0.25-0.40 |
| Sin commits en la sesión actual | 0.50 |

### OUTPUT
```json
{
  "tasks": [
    {
      "task_id": "TASK-001",
      "risks": {
        "curvature": 0.05,
        "pre_churn": 0.0,
        "commit_trust": 0.1
      }
    }
  ]
}
```

---

## FASE 5: Generar archivos + Enforcement

### INPUT
Todas las fases anteriores ensambladas.

### PROCESO
5.1 Construir `plan.json` siguiendo `schema/plan.schema.json`.

5.2 Construir `progress.json` siguiendo `schema/progress.schema.json`.

5.3 Crear directorio del chain:
```bash
mkdir -p <chain_dir>
```

5.4 Escribir archivos:
```bash
echo "{...plan.json...}" > <chain_dir>/plan.json
echo "{...progress.json...}" > <chain_dir>/progress.json
```

5.5 Validación ENFORCEMENT:
```bash
python3 scripts/validate-schema.py <chain_dir>/plan.json schema/plan.schema.json
python3 scripts/validate-schema.py <chain_dir>/progress.json schema/progress.schema.json
```

5.6 Si validación falla → eliminar archivos, exit 2 (sprint no creado).

### OUTPUT
```
<chain_dir>/plan.json        — Plan inmutable con tareas, riesgos, predicates
<chain_dir>/progress.json    — Estado mutable (task_states, trail, next_action)
```

---

## ADVANCE: TASK LOOP + Loop Prompting + Calibración

> 🔧 **API Pública**: `walker.py transition proceed` (desde USER_GATE → ADVANCE_TASK)
>
> Este comando hace que walker.py invoque internamente `task.py advance`:
> 1. `_internal/enforce.py` — Gate de coerción (absorbido en task.py)
> 2. `_internal/score.py` — Cálculo de score FASE 5 (absorbido en task.py)
> 3. `_internal/advance.py` — TASK LOOP 3-capas + Qlty gates + confirmación (absorbido en task.py)
>
> 🔧 **Self-sufficient**: El output de `walker.py transition proceed` contiene las
> instrucciones de decisión para cada exit code. Lee el stdout del comando
> — no necesitas el SKILL.md para saber qué hacer.

### Diagrama de flujo completo (ejecutado internamente por `task.py advance`)

```
task.py advance <chain_dir> <task_id>   ← invocado por walker.py
  │
  ├── 0. ROLE IDENTITY (--act-as po|dev|qa)
  │     ├── Banner de identidad: "Actuando como: PO/Dev/QA"
  │     ├── Determina qué gates aplicar (ver ROLE GATES abajo)
  │     └── Sin --act-as → default "dev"
  │
  ├── 1. ENFORCEMENT (absorbido: _internal/enforce.py)
  │     ├── next_action: la tarea debe ser la next_action de progress.json
  │     │     └── ⚠️ Si tarea está en ready_for_review, next_action se salta
  │     ├── curvature_budget: curvature_risk ≤ 0.50
  │     ├── distance_guard: debe tener predicates de validación
  │     └── backlog_required: tareas fuera de plan.json deben estar en backlog
  │
  ├── 2. SCORE (absorbido: _internal/score.py)
  │     └── Calcular FASE 5 score para priorización
  │
  ├── 3. ROLE GATES (controlado por --act-as)
  │     ├── Gate 1 (human-confirm): solo --act-as po puede pasar --human-confirm
  │     │     └── ❌ Dev/QA intentan human-confirm → error + sugerencia
  │     ├── Gate 2 (qlty-check): --act-as qa NO puede saltarse qlty
  │     │     └── ❌ QA sin qlty o con --skip-qlty → error
  │     └── Gate 3 (ready_for_review): --act-as po puede confirmar sin re-evaluar
  │           └── ✅ PO en ready_for_review → salta enforcement + task loop
  │
  ├── 4. TASK LOOP (absorbido: _internal/advance.py)
  │     ├── Capa 1: loop_verification (grep/pytest)
  │     ├── Capa 2: acceptance_criteria (criterios alto nivel)
  │     └── Capa 3: quality_gates (barreras no negociables)
  │
  ├── 5. QLTY GATES (absorbido: _internal/qlty_check.py)
  │     └── 8 quality gates automáticos sobre impacted_artifacts
  │
  ├── 6. ¿Resultado?
  │     │
  │     ├── ENFORCEMENT BLOCKED → exit 2
  │     │     └── Usar --force para override
  │     │
  │     ├── PARTIAL (exit 1)
  │     │     └── REFINE: corregir checks fallidos, repetir
  │     │
  │     └── ACHIEVED
  │           │
  │           ├── ¿--confirm o --human-confirm?
  │           │     │
  │           │     ├── NO → ready_for_review (exit 3)
  │           │     │     ├── Mostrar review_prompt al LLM
  │           │     │     ├── LLM revisa resultados
  │           │     │     ├── ¿Satisfecho? → task.py advance --act-as dev --confirm
  │           │     │     └── ¿No? → REFINE código → task.py advance again
  │           │     │
  │           │     └── SÍ → avanza a confirmación
  │           │
  │           └── CONFIRMAR + POST-WRITE VALIDATION
  │                 ├── Actualizar progress.json (task_states, trail, next_action)
  │                 ├── Validar progress.json contra schema (ENFORCEMENT)
  │                 ├── Si falla validación → bloquea avance, exit 2
  │                 └── Éxito → exit 0
  │
  └── Paso final: Copiar a current.json
```

### Schema de la calibración en trail

```json
{
  "at": "2026-06-25T15:00:00-03:00",
  "distance": 0.44,
  "event": "TASK-001 completed",
  "tasks": ["TASK-001"],
  "calibration": {
    "state": {
      "expected_satisfied": 4,
      "actual_satisfied": 3,
      "type": "under_estimated",
      "gap_detail": [
        {
          "predicate": "function get_call_chain en CodeGraphAdapter",
          "expected": true,
          "actual": false,
          "cause": "dependencia con TASK-004 no resuelta"
        }
      ]
    },
    "delta": {
      "expected_reduction": 0.31,
      "actual_reduction": 0.41,
      "error": -0.10,
      "error_pct": 24.4,
      "error_type": "under_estimated",
      "cause": {
        "category": "reasoning",
        "evidence": "get_call_chain dependía de TASK-004 no capturado en el plan"
      },
      "learnable": true,
      "contributors": [
        "se subestimó el impacto de eliminar código muerto durante la migración"
      ]
    },
    "strategy": {
      "optimal": true
    }
  }
}
```

### Reglas

- La calibración es **descriptiva**, no correctiva. No modifica el plan ni cancela tareas.
- Si una tarea satisface predicates adicionales no declarados en `validation.predicates[]`, se registran como `over_estimated`.
- Los `adjustments[]` son sugerencias; la aplicación la decide el humano o el próximo sprint.
- El `model.parameters` en `current` acumula los valores vigentes de los factores de ajuste.
- **Separación de causas**: Solo `cause.category` en `reasoning` o `execution` es `learnable=true`.
  Errores `external`, `environment` o `unknown` se registran como contexto del proyecto
  pero no alimentan el modelo de navegación. Esto evita que el GPS aprenda de ruido externo.

---

## Self-Sufficient Output

Todos los comandos de `walker.py` producen en su **stdout** las instrucciones
que el LLM necesita. El output de cada comando es auto-suficiente:

| Comando walker.py | Script interno | Exit | El output dice |
|---|---|---|---|
| `walker.py transition proceed` (ADVANCE_TASK) | `task.py advance` | 3 | Coverage, gaps, QGates + `→ CONFIRMAR / → REFINE` |
| `walker.py transition proceed` (ADVANCE_TASK) | `task.py advance` | 1 | Qué falló (must_exist/must_not_exist/test) + recovery |
| `walker.py transition proceed` (ADVANCE_TASK) | `task.py advance` | 0 | Siguiente tarea o → cerrar sprint |
| `walker.py verify` | `task.py qlty-check` | 1 | Qué métrica falló + hint de corrección |
| `walker.py transition health_ok` | `spring.py close` | 0/1 | Composición + `→ nuevo delta / → backlog` |
| `walker.py status` | `spring.py status` | 0 | Estado del sprint + transiciones disponibles |
| `walker.py transition sprint_created` (CREATE_SPRINT) | `spring.py create` | 2 | Error de validación con campos exactos + `→ corrige y reintenta` |
| `walker.py health` | `validate-schema.py` | 1 | Errores exactos + `→ corrige y reintenta` |
