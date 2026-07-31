# 📋 Escenarios de Uso — Delta Skill

> Ejemplos prácticos de cómo navegar el workflow usando `walker.py` para diferentes
> tipos de tareas. El LLM **solo ejecuta `walker.py`**. Internamente, `walker.py`
> delega en `spring.py` y `task.py` según el nodo del workflow.
>
> 🔧 **Self-Sufficient**: Cada comando produce en su stdout las instrucciones
> que el LLM necesita para decidir el siguiente paso. Lee el output del comando
> — no necesitas consultar el SKILL.md para saber qué hacer con un exit code.
>
> 📌 **Workflow**: Cada escenario describe la secuencia de transiciones que el
> LLM ejecuta con `walker.py transition <gate>`. El workflow de 22 nodos (ver
> [SKILL.md](SKILL.md#flujo-de-trabajo)) guía cada paso con roles, gates de
> confirmación humana y validaciones automáticas.

---

## Escenario 1: Feature — "Implementar jackpot reset"

**Invocación**: `skill:delta Implementar lógica de reseteo de jackpot en src/game/reset.py`

**Pipeline ejecutado** (el LLM navega el workflow de 22 nodos):

### 1. Crear sprint

El LLM navega hasta CREATE_SPRINT:
```bash
walker.py status
# → INIT → CHECK_SPRINTS → CREATE_SPRINT
# En CREATE_SPRINT: el on_entry_message indica crear el sprint
# El LLM usa los datos del goal para definir tareas, predicates y riesgos
```

El LLM sigue las instrucciones del nodo CREATE_SPRINT (manual) para definir:
- `--goal`: "Implementar lógica de reseteo de jackpot"
- `--predicates`: con must_exist, must_not_exist, invariants
- `--tasks`: 3 tareas con objective, type, domain, result_state, riesgos

Luego transiciona:
```bash
walker.py transition sprint_created
# → SPRINT_ACTIVE (auto: muestra dashboard)
```

**Output:**
```
🚀 Sprint creado: <chain_id>
   Proyecto:    game_reset
   Goal:        Implementar lógica de reseteo de jackpot
   Distancia:   0.75 / 1.0
   Tareas:      0/3
   Riesgo:      bajo (0.06)

📋 Próxima acción:
   TASK-001: Crear JackpotConfig en src/game/models.py
   📈 Score: 0.283 | ⚠️ Riesgo: 0.05
```

### 2. Procesar cada tarea (workflow completo)

Cada tarea sigue el workflow completo con gates de confirmación humana:

```bash
# Transicionar desde SPRINT_ACTIVE
walker.py transition has_pending
# → TASK_PENDING

# El LLM actúa como Product Owner (nodo PO_REVIEW)
walker.py transition po_approved
# → IN_PROGRESS (rol: Developer)
# → RESEARCH (auto: genera contexto)
# → ANALYSIS_GATE (rol: Analista)
# → ANALYSIS_REVIEW (rol: Tech Lead)

# 🛑 USER_GATE: presentar informe al usuario
# El LLM muestra el plan de implementación:
#   "TASK-001: Crear JackpotConfig en src/game/models.py
#    - Archivos: src/game/models.py
#    - Predicates: class JackpotConfig con game_id, amount, currency
#    - Riesgo: curvature=0.05"
# El usuario decide:
walker.py transition proceed
# → ADVANCE_TASK (auto: implementa)
# → DONE_REVIEW (rol: Tech Lead)
# → CODE_REVIEW (rol: Code Reviewer)

# 🛑 REPORT_GATE: presentar resultado al usuario
# El LLM muestra el resultado de la implementación:
#   "JackpotConfig creada con game_id, amount, currency
#    - Predicates verificados: ✅
#    - Archivos modificados: src/game/models.py"
# El usuario decide:
walker.py transition proceed
# → VERIFY (rol: QA)
# → PO_ACCEPTANCE (rol: Product Owner)
# → SPRINT_ACTIVE ✅
```

Repetir para TASK-002 y TASK-003.

### 3. Cerrar sprint
```bash
# Desde SPRINT_ACTIVE, cuando no hay tareas pendientes:
walker.py transition all_done
# → CLOSE_CHECK (auto: health check)
walker.py transition health_ok
# → CLOSE_SPRINT ✅
```

### Flujo completo de avance (resumen)

```
walker.py transition has_pending  # SPRINT_ACTIVE → TASK_PENDING
walker.py transition po_approved  # TASK_PENDING → PO_REVIEW → IN_PROGRESS
# (research + analysis + analysis_review ocurren automáticamente)
walker.py transition proceed      # USER_GATE → ADVANCE_TASK
# (done_review + code_review ocurren)
walker.py transition proceed      # REPORT_GATE → VERIFY
# (po_acceptance ocurre)
walker.py transition all_done     # SPRINT_ACTIVE → CLOSE_CHECK
walker.py transition health_ok    # CLOSE_CHECK → CLOSE_SPRINT
```

> 🔧 En cada nodo, `walker.py status` muestra las transiciones disponibles
> y el contexto actual. No necesitas memorizar el grafo — el walker te guía.

---

## Escenario 2: Bugfix — "Fix login validation"

**Invocación**: `skill:delta Arreglar validación de login que no chequea tokens expirados`

### Crear sprint (vía CREATE_SPRINT)
El LLM sigue el flujo INIT → CHECK_SPRINTS → CREATE_SPRINT, crea el sprint
con goal, predicates y 2 tareas, luego transiciona a SPRINT_ACTIVE.

### Procesar tareas con Qlty automático
```bash
walker.py transition has_pending    # → TASK_PENDING
walker.py transition po_approved    # → IN_PROGRESS
# (research → analysis_gate → analysis_review → USER_GATE →)
walker.py transition proceed        # USER_GATE → ADVANCE_TASK
# TASK-001 es refactor → ADVANCE_TASK ejecuta Qlty gates automáticamente
# (done_review → code_review → REPORT_GATE →)
walker.py transition proceed        # REPORT_GATE → VERIFY
# (po_acceptance → SPRINT_ACTIVE)

# Repetir para TASK-002 (bugfix)
walker.py transition has_pending
...

# Cerrar
walker.py transition all_done
walker.py transition health_ok      # → CLOSE_SPRINT
```

> 🛠️ **Qlty automático**: Para tareas `refactor` y `optimization`, el nodo
> ADVANCE_TASK ejecuta automáticamente Qlty quality gates (CC, LCOM, duplicación,
> etc.). Ver [`tools.json`](tools.json).

---

## Escenario 3: Refactor — "Extraer lógica de billing"

**Invocación**: `skill:delta Refactorizar BillingService en módulos separados`

### Crear sprint (vía CREATE_SPRINT)
3 tareas refactor con curvature más alto (0.25-0.30). El LLM define el plan
y transiciona a SPRINT_ACTIVE.

### Procesar tareas (Qlty automático para refactors)
```bash
walker.py transition has_pending
walker.py transition po_approved
walker.py transition proceed        # USER_GATE → ADVANCE_TASK
walker.py transition proceed        # REPORT_GATE → VERIFY
# ... repetir para TASK-002, TASK-003
walker.py transition all_done
walker.py transition health_ok
```

> 🛠️ **Qlty automático**: Las 3 tareas son `refactor`. ADVANCE_TASK ejecuta
> automáticamente Qlty quality gates (cyclo ≤ 60, LCOM ≤ 20, Ce ≤ 10) después
> de cada implementación. Si algún gate falla, el flujo vuelve a REFINE.

---

## Escenario 4: Progreso — "¿Cómo vamos?"

**Invocación**: `skill:delta --status`

### Dashboard rápido
```bash
walker.py status
```

**Output esperado:**
```
📊 Estado del sprint: <chain_id>
   Nodo actual: SPRINT_ACTIVE
   Progreso: 2/3 tareas completadas
   Siguiente: TASK-003 — Crear reset_jackpot()
   ⚠️ Riesgo: curvature=0.10 (bajo)
   Transiciones disponibles:
     → has_pending (TASK-003)
     → all_done
     → edit_plan
```

---

## Escenario 5: Proyecto completo — "Planifica el proyecto de auth"

**Invocación**: `skill:delta Planificar módulo de autenticación completo: login, registro, recuperación`

### Crear sprint (vía CREATE_SPRINT)
5 tareas: schemas, service, endpoints, recovery, tests. El LLM define el plan
completo siguiendo el flujo INIT → CHECK_SPRINTS → CREATE_SPRINT → SPRINT_ACTIVE.

### Procesar tareas
```bash
# TASK-001: Schemas de auth
walker.py transition has_pending
walker.py transition po_approved
walker.py transition proceed        # USER_GATE
walker.py transition proceed        # REPORT_GATE
# → SPRINT_ACTIVE

# TASK-002: Auth service
walker.py transition has_pending
walker.py transition po_approved
walker.py transition proceed        # USER_GATE
walker.py transition proceed        # REPORT_GATE
# → SPRINT_ACTIVE

# ... TASK-003, TASK-004, TASK-005 (mismo patrón)

# Cerrar
walker.py transition all_done
walker.py transition health_ok
```

---

## Anexo: Comandos de walker.py por escenario

Cada escenario usa `walker.py` como interfaz única. Internamente, walker.py
delega en `spring.py` (creación/cierre de sprints) y `task.py` (avance de
tareas) según el nodo del workflow.

| Escenario | Comandos walker.py |
|---|---|
| **Feature** (Esc. 1, 5) | `status` → `transition sprint_created` → `transition has_pending` → `transition po_approved` → `transition proceed` (USER_GATE) → `transition proceed` (REPORT_GATE) → `transition all_done` → `transition health_ok` |
| **Bugfix** (Esc. 2) | `status` → `transition sprint_created` → `transition has_pending` → `transition po_approved` → `transition proceed` (USER_GATE) → `transition proceed` (REPORT_GATE) → `transition all_done` → `transition health_ok` |
| **Refactor** (Esc. 3) | `status` → `transition sprint_created` → `transition has_pending` → `transition po_approved` → `transition proceed` (USER_GATE) → `transition proceed` (REPORT_GATE) → `transition all_done` → `transition health_ok` |
| **Progreso** (Esc. 4) | `status` |

> **Nota para refactors**: El nodo ADVANCE_TASK ejecuta automáticamente Qlty gates
> para todo tipo de tarea. Para refactors, verifica cyclo ≤ 60, LCOM ≤ 20,
> Ce ≤ 10, 0 production bugs, 0 security issues, etc.

### Self-Sufficient

Todos los comandos de `walker.py` producen en su **stdout** las instrucciones
que el LLM necesita para decidir el siguiente paso. No es necesario consultar
el SKILL.md para saber qué hacer con un exit code — el comando mismo lo dice.

Ejemplos concretos de outputs:
- `walker.py status` → `📍 USER_GATE` + `Transiciones: proceed, refine, cancel`
- `walker.py transition proceed` → `✅ ADVANCE_TASK` + `Implementando TASK-001...`
- `walker.py transition proceed` (REPORT_GATE con dependencias pendientes) → `❌ Gate validation: dependencias TASK-003 sin resolver`
- `walker.py resolve` → `🔍 Diagnóstico: workflow desactualizado → migrado a 22 nodos`
