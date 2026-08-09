# 🧭 Delta Spring — Documentación del Sistema

> **Delta Spring** es el sistema de orquestación de flujos de desarrollo de software
> para agentes LLM. Modela el trabajo como una transformación del estado actual del
> código (`G_actual`) hacia un estado deseado (`G_objetivo`) mediante un grafo de
> workflow con nodos, roles, gates de confirmación humana y auto-migración.

---

## Tabla de Contenidos

1. [Problema](#1-problema)
2. [Estado del Arte](#2-estado-del-arte)
3. [Propuesta de Solución](#3-propuesta-de-solución)
4. [Diseño](#4-diseño)
5. [Conclusiones](#5-conclusiones)

---

## 1. Problema

### 1.1 El problema fundamental

Los agentes LLM carecen de un modelo estructurado para orquestar el desarrollo de
software. Sin un workflow explícito, el agente:

- **Adivina** qué paso sigue en lugar de saberlo
- **Salta** pasos críticos (code review, QA, aprobación PO)
- **No persiste** contexto entre sesiones — cada interacción empieza de cero
- **Actúa sin supervisión humana** — implementa sin confirmación en puntos críticos
- **No tiene trazabilidad** — no se sabe qué decisiones se tomaron ni por qué
- **Mezcla roles** — el mismo agente diseña, implementa, revisa y aprueba sin distinción

### 1.2 Síntomas en la práctica

| Síntoma | Consecuencia |
|---------|-------------|
| El agente salta de análisis a implementación sin revisión | Código que no cumple los requisitos |
| No hay code review explícito | Deuda técnica, estilo inconsistente |
| No hay QA gates | Bugs que llegan a producción |
| El agente usa `goto` para "destrabar" el flujo | Se pierde la trazabilidad del workflow |
| No hay confirmación humana antes de implementar | El usuario descubre cambios no solicitados |
| El workflow es rígido (14 nodos fijos) | No se adapta a proyectos con diferentes necesidades |

### 1.3 Por qué un workflow graph

Un workflow graph (en lugar de un pipeline lineal) permite:

- **Múltiples caminos** para una misma tarea (contexto suficiente vs investigación previa)
- **Ciclos de refinamiento** (volver a análisis si la implementación falla)
- **Gates condicionales** (saltar PO review si ya fue aprobado)
- **Roles explícitos** (Developer, Code Reviewer, QA, PO, User)
- **Auto-migración** (el workflow se actualiza solo cuando cambia)

---

## 2. Estado del Arte

### 2.1 Versión actual: v3 — 22 nodos, 6 roles

El sistema opera con **22 nodos** conectados por **50 edges**, manejados por un
engine central (`walker.py`) que el LLM usa como única interfaz.

#### 2.1.1 Los 22 nodos

| # | Nodo | Rol | Modo | Propósito |
|---|------|-----|------|-----------|
| 1 | INIT | — | Manual | Punto de entrada. No hay sprint activo. |
| 2 | CHECK_SPRINTS | — | Auto | Lista sprints disponibles. Post-gate determina si existen. |
| 3 | CREATE_SPRINT | — | Manual | Crea un sprint con goal + tareas + predicates. |
| 4 | SPRINT_ACTIVE | — | Auto | Dashboard del sprint. Muestra tareas y estados. |
| 5 | TASK_PENDING | — | Juicio LLM | Evalúa qué acción tomar para la tarea actual. |
| 6 | PO_REVIEW | Product Owner | Juicio LLM | PO revisa el ALCANCE (objective + predicates de negocio). NO evalua contexto técnico. Puede pedir `po_needs_context` → RESEARCH si necesita ver las 6 capas antes de decidir. |
| 7 | IN_PROGRESS | Developer | Juicio LLM | Indica trabajo activo en la tarea. |
| 8 | RESEARCH | — | Auto | Genera las 6 capas de contexto. |
| 9 | ANALYSIS_GATE | Analista | Juicio LLM | Análisis recursivo 6→3 capas. |
| 10 | ANALYSIS_REVIEW | Tech Lead | Juicio LLM | Revisa y aprueba el análisis. |
| **11** | **USER_GATE** | **User** | **Manual** | **✅ Detiene el flujo. Presenta informe al usuario. Espera PROCEED/REFINE/CANCEL.** |
| 12 | ADVANCE_TASK | — | Auto | Implementa la tarea. Post-gate evalúa resultado. |
| 13 | DONE_REVIEW | Tech Lead | Juicio LLM | Revisa el informe de implementación. |
| 14 | CODE_REVIEW | Code Reviewer | Juicio LLM | Revisa el código implementado. |
| **15** | **REPORT_GATE** | **User** | **Manual** | **✅ Detiene el flujo. Presenta informe post-implementación. Valida dependencias antes de proceed.** |
| 16 | VERIFY | QA / Tester | Juicio LLM | Revisa la implementación contra predicates. |
| 17 | PO_ACCEPTANCE | Product Owner | Juicio LLM | PO acepta o rechaza el resultado final. |
| 18 | REFINE | Diagnosticador | Juicio LLM | Determina qué necesita refinamiento. |
| 19 | EDIT_PLAN | — | Manual | Modifica el plan del sprint. |
| 20 | CLOSE_CHECK | — | Auto | Health check del sprint. Post-gate evalúa cierre. |
| 21 | HANDLE_PENDING | Scrum Master | Juicio LLM | Decide qué hacer con tareas no completadas. |
| 22 | CLOSE_SPRINT | — | Auto | Cierra el sprint. |

#### 2.1.2 Los 6 roles

| Rol | Nodos donde actúa |
|-----|-------------------|
| **Developer** | IN_PROGRESS |
| **Analista** | ANALYSIS_GATE |
| **Tech Lead** | ANALYSIS_REVIEW, DONE_REVIEW |
| **Code Reviewer** | CODE_REVIEW |
| **QA / Tester** | VERIFY |
| **Product Owner** | PO_REVIEW, PO_ACCEPTANCE |
| **Diagnosticador** | REFINE |
| **Scrum Master** | HANDLE_PENDING |
| **User** | **USER_GATE, REPORT_GATE** |

#### 2.1.3 Modos de operación

| Modo | Qué pasa | Rol del LLM |
|------|----------|-------------|
| **auto_execute** | Walker ejecuta el script automáticamente al llegar al nodo | Solo observa el output |
| **juicio LLM** | No hay script. El LLM decide el siguiente gate según el rol | Actúa según el rol del nodo |
| **manual** | No hay script ni juicio. Walker muestra `on_entry_message` con instrucciones | Sigue las instrucciones al pie de la letra |

Los nodos USER_GATE y REPORT_GATE son **manuales**: el LLM debe presentar un informe
al usuario y esperar su decisión. No puede avanzar sin interacción humana.

### 2.2 Arquitectura del engine

```
.cognitive/projects/<project-id>/
├── project.json                 ← Perfil del proyecto
├── workflow.json                ← 22 nodos, 50 edges (auto-migrable)
├── backlog.json                 ← Tareas movidas fuera del sprint
└── delta/
    └── <chain-id>/
        ├── plan.json            ← Plan del sprint (tareas, predicates)
        ├── progress.json        ← Estado de cada tarea + decision_log
        └── .delta-walker.json   ← Estado del walker (persistido automáticamente)
```

### 2.3 Auto-migración

Cuando se ejecuta `walker.py status` o `walker.py resolve`, el engine:

1. Compara los nodos existentes contra la lista canónica de 22 nodos
2. Agrega nodos faltantes con configuración por defecto
3. Actualiza configs de nodos existentes (post_gate, entry_action, data_contract)
4. Reemplaza edges obsoletos por la versión canónica
5. Mantiene edges personalizados que no entran en conflicto

Esto garantiza que proyectos creados con versiones anteriores (14 nodos) se
actualicen sin intervención manual.

### 2.4 Mecanismo de persistencia

| Archivo | Contenido | Quién lo escribe |
|---------|-----------|------------------|
| `plan.json` | Tareas del sprint con objetivos, predicates, artefactos | `spring.py create`, `EDIT_PLAN` |
| `progress.json` | Estados de tareas, decision_log, contract_data, walker_data | Walker automáticamente en cada transición |
| `.delta-walker.json` | Nodo actual, historial, contexto | Walker en cada transición |
| `backlog.json` | Tareas movidas fuera del sprint | `walker.py backlog`, HANDLE_PENDING |
| `workflow.json` | Grafo de nodos y edges | `delta-init-project.py`, auto-migración |

### 2.5 Decision log

Todas las decisiones clave se registran en:

```
progress.json.current.walker_data.decision_log[]
```

Cada entrada contiene: `{from, gate, task_id, reason, at}`. Esto permite al
workflow recordar contexto incluso si el estado del walker se pierde.

### 2.6 Lo que existía antes (v2 → v3)

| Aspecto | v2 (14 nodos) | v3 (22 nodos) |
|---------|---------------|---------------|
| Nodos | 14 | 22 |
| Roles | Developer, Code Reviewer, QA, PO | + User, Tech Lead, Analista, Diagnosticador, Scrum Master |
| User gates | ❌ No existían | ✅ USER_GATE + REPORT_GATE con PROCEED/REFINE/CANCEL |
| Confirmación humana | ❌ Nunca | ✅ Obligatoria en USER_GATE y REPORT_GATE |
| Auto-migración | ❌ No existía | ✅ Sí, vía `_migrate_workflow` |
| Data contract | ❌ No existía | ✅ Sí, por nodo (fields + descripción) |
| Cancel task | ❌ No existía | ✅ Sí, con `_mark_task_cancelled` |
| Dependencias | ❌ No se validaban | ✅ `check-dependencies` en REPORT_GATE |
| Contratos por edge | ❌ No existían | ✅ Contract-First con input/output/pre/post |
| Resolve command | ❌ No existía | ✅ Sí, con detección y estrategias |

---

## 3. Propuesta de Solución

### 3.1 Escenarios de uso

Los siguientes escenarios describen cómo el sistema resuelve casos reales de
desarrollo de software. Cada escenario cubre un aspecto diferente del workflow.

---

#### Escenario A: Feature completa con confirmación humana

**Contexto:** El equipo necesita implementar una nueva funcionalidad. Es la
primera vez que usan el workflow con gates de usuario.

**Flujo:**

```
Usuario: "Implementar módulo de notificaciones push"

1. INIT → CHECK_SPRINTS (auto: lista sprints)
2. CREATE_SPRINT (LLM crea sprint con goal + 3 tareas)
3. SPRINT_ACTIVE (auto: dashboard)
4. TASK_PENDING → PO_REVIEW
   → PO aprueba alcance: "po_approved"
5. TASK_PENDING → IN_PROGRESS → ANALYSIS_GATE
   → LLM hace análisis 6→3 capas
6. ANALYSIS_REVIEW → advance_confirm
7. USER_GATE ← 🛑 EL WORKFLOW SE DETIENE
   → LLM presenta informe al usuario:
     "TASK-001: Crear NotificationService
      - Archivos: src/services/notification.ts
      - Predicates: sendNotification(), registerToken()
      - Riesgo: curvature=0.3"
   → Usuario: "PROCEDER"
   → LLM ejecuta: walker.py transition proceed
8. ADVANCE_TASK (auto: implementa)
9. DONE_REVIEW → CODE_REVIEW
   → Code Reviewer: "review_ok"
10. REPORT_GATE ← 🛑 EL WORKFLOW SE DETIENE
    → LLM presenta informe de implementación:
      "Se creó NotificationService con sendNotification() y registerToken()
       - 2 predicates verificados: ✅
       - Archivos modificados: 2 creados, 1 modificado
       - Decisiones: Se usó Firebase Cloud Messaging por compatibilidad"
    → Usuario: "PROCEDER"
    → LLM ejecuta: walker.py transition proceed
11. VERIFY → PO_ACCEPTANCE → SPRINT_ACTIVE
```

**Lo que el sistema garantiza:**
- ✅ El usuario ve el análisis ANTES de implementar
- ✅ El usuario ve el resultado ANTES de que pase a QA
- ✅ El LLM no puede saltarse estos pasos
- ✅ Cada decisión queda registrada en decision_log

---

#### Escenario B: Refinamiento por observaciones del usuario

**Contexto:** El usuario revisa el análisis en USER_GATE y no está de acuerdo
con el enfoque. Quiere ajustar criterios antes de implementar.

**Flujo:**

```
... → USER_GATE
→ LLM presenta informe
→ Usuario: "REFINO. En vez de Firebase, usad APNs directo"
→ LLM ejecuta: walker.py transition refine --notes "usar APNs directo en vez de Firebase"
→ PO_REVIEW (LLM actúa como PO)
  → LLM actualiza la tarea: cambia impacted_artifacts, ajusta predicates
  → LLM ejecuta: walker.py transition po_approved_advance
→ ADVANCE_TASK (implementa con APNs)
... → REPORT_GATE → Usuario: "PROCEDER"
```

**Lo que el sistema garantiza:**
- ✅ El usuario puede refinar sin reiniciar todo el flujo
- ✅ El LLM aplica los cambios actuando como PO (rol explícito)
- ✅ El refinamiento va directo a implementación sin re-analizar
- ✅ La observación del usuario se persiste en decision_log

---

#### Escenario C: Cancelación de tarea

**Contexto:** El usuario decide que una tarea ya no es necesaria.

**Flujo:**

```
... → USER_GATE
→ LLM presenta informe
→ Usuario: "CANCELO. Este feature ya no es prioritario"
→ LLM ejecuta: walker.py transition cancel --reason "feature deprioritized"
→ SPRINT_ACTIVE (walker marca TASK-001 como cancelled en progress.json)
  → TASK-001: cancelled
  → decision_log: {gate: "cancel", reason: "feature deprioritized", ...}
```

**Lo que el sistema garantiza:**
- ✅ La tarea se marca como `cancelled` (no como `completed`)
- ✅ El motivo queda registrado en decision_log
- ✅ El sprint continúa con las tareas restantes
- ✅ Se puede recuperar la tarea del backlog si es necesario

---

#### Escenario D: Workflow heredado con auto-migración

**Contexto:** Un proyecto existente fue creado con la versión anterior del
workflow (14 nodos, sin USER_GATE/REPORT_GATE). Un agente nuevo llega y
encuentra el workflow desactualizado.

**Flujo:**

```
→ Agente ejecuta: walker.py status --project legacy-app
  → [migrate] workflow.json actualizado a v3 (22 nodos, 50 edges)
  → El engine agrega USER_GATE y REPORT_GATE automáticamente

→ Agente ejecuta: walker.py resolve --project legacy-app
  → Diagnóstico:
    - Workflow migrado: ✅
    - Estado reconstruido: ⚠️
    - Nodos humanos no visitados: USER_GATE, REPORT_GATE, CODE_REVIEW
  → Feedback:
    "Las tareas nuevas pasarán por USER_GATE y REPORT_GATE.
     Las tareas existentes mantienen su estado actual."
```

**Lo que el sistema garantiza:**
- ✅ El proyecto se migra sin intervención manual
- ✅ El agente recibe feedback claro de lo que pasó
- ✅ Las tareas nuevas usan el workflow completo
- ✅ Las tareas existentes no se ven afectadas

---

#### Escenario E: Code Review con correcciones

**Contexto:** El Code Reviewer encuentra problemas en el código y lo rechaza.

**Flujo:**

```
... → ADVANCE_TASK → DONE_REVIEW → CODE_REVIEW
  → Code Reviewer: "review_fail --notes 'faltan tests, nombres inconsistentes'"
  → ADVANCE_TASK (el LLM aplica las correcciones)
  → DONE_REVIEW → CODE_REVIEW
  → Code Reviewer: "review_ok"
  → REPORT_GATE
```

**Lo que el sistema garantiza:**
- ✅ El código pasa por revisión antes de llegar al usuario
- ✅ Las notas del revisor se persisten
- ✅ El ciclo de corrección puede repetirse hasta que pase

---

#### Escenario F: Dependencias no resueltas en REPORT_GATE

**Contexto:** Una tarea en REPORT_GATE tiene dependencias que no se han
completado. El usuario quiere proceder pero el gate lo bloquea.

**Flujo:**

```
... → REPORT_GATE
→ Usuario: "PROCEDER"
→ LLM ejecuta: walker.py transition proceed
  → Gate validation FAILED:
    "Tarea TASK-005 tiene dependencias no resueltas: TASK-003"
→ LLM informa al usuario:
  "No se puede proceder porque TASK-003 (API Gateway) no está completada.
   Opciones:
   - REFINAR para ajustar dependencias
   - CANCELAR la tarea"
→ Usuario: "REFINO. La dependencia ya no aplica, la resuelvo aparte"
→ LLM ejecuta: walker.py transition refine --notes "remover dependencia TASK-003"
  → PO_REVIEW → actualiza depends_on → po_approved_advance → ADVANCE_TASK
```

**Lo que el sistema garantiza:**
- ✅ No se permite proceder si hay dependencias sin resolver
- ✅ El error es claro e incluye la lista de dependencias faltantes
- ✅ El usuario tiene alternativas (refine, cancel)
- ✅ El LLM no puede ignorar la validación

---

#### Escenario G: Destrabe de sprint en CLOSE_CHECK

**Contexto:** Un agente anterior dejó el sprint en CLOSE_CHECK pero hay tareas
pendientes. El nuevo agente no sabe cómo continuar.

**Flujo:**

```
→ Agente ejecuta: walker.py status
  → Current state: CLOSE_CHECK
  → Tareas: TASK-010 pending, TASK-011 pending

→ Agente ejecuta: walker.py resolve
  → Diagnóstico:
    - CLOSE_CHECK con 2 tareas pendientes: TASK-010, TASK-011
  → Feedback:
    "Usa 'walker.py transition health_fail' para volver a SPRINT_ACTIVE
     y procesar las tareas pendientes con el flujo completo."

→ Agente ejecuta: walker.py transition health_fail
  → SPRINT_ACTIVE
  → transition has_pending → TASK_PENDING → ... (flujo normal)
```

**Lo que el sistema garantiza:**
- ✅ El agente no usa `goto` ciegamente
- ✅ `resolve` diagnostica y sugiere la transición natural
- ✅ Las tareas pendientes se procesan con USER_GATE y REPORT_GATE
- ✅ El sprint no se cierra con tareas sin completar

---

#### Escenario H: Ciclo completo con múltiples roles

**Contexto:** Una tarea compleja que requiere la intervención de todos los roles.

**Flujo:**

```
TASK_PENDING
  → PO_REVIEW (PO aprueba)
  → RESEARCH (genera contexto) → ANALYSIS_GATE (analista)
  → ANALYSIS_REVIEW (Tech Lead aprueba)
  → USER_GATE (Usuario: PROCEDER) ← CONFIRMACIÓN HUMANA
  → ADVANCE_TASK (Developer implementa)
  → DONE_REVIEW (Tech Lead revisa informe)
  → CODE_REVIEW (Code Reviewer: review_ok) 
  → REPORT_GATE (Usuario: PROCEDER) ← CONFIRMACIÓN HUMANA
  → VERIFY (QA: verify_ok)
  → PO_ACCEPTANCE (PO: po_accepted)
  → SPRINT_ACTIVE ✅
```

Roles que participaron: **PO, Developer, Analista, Tech Lead, Code Reviewer,
User, QA** — 7 roles diferentes para una sola tarea.

---

## 4. Diseño

### 4.1 Módulos

```
delta/
├── SKILL.md                  ← Instrucciones para el LLM (qué hacer en cada nodo)
├── teoria.md                  ← Fundamento teórico (modelo GPS cognitivo)
├── delta-spring.md            ← ← ESTE DOCUMENTO (documentación del sistema)
├── delta-scenarios.md         ← Escenarios de uso de spring.py/task.py (v2)
├── delta-pipeline.md          ← Pipeline de ejecución detallado (v2)
│
├── schema/                    ← Schemas JSON de validación
│   ├── plan.schema.json       ← Schema del plan del sprint (22 campos por tarea)
│   ├── progress.schema.json   ← Schema del progreso
│   ├── project.schema.json    ← Schema del proyecto
│   ├── workflow.schema.json   ← Schema del workflow (nodes, edges, contracts)
│   └── tools.schema.json      ← Schema de herramientas
│
├── scripts/                   ← Scripts del engine
│   ├── walker.py              ← ENGINE PRINCIPAL (único que ejecuta el LLM)
│   ├── spring.py              ← Gestión de sprints (crear, listar, cerrar)
│   ├── task.py                ← Gestión de tareas (research, advance, verify)
│   ├── delta-gate-check.py    ← Validación de gates (pending-count, check-dependencies)
│   ├── delta-transform.py     ← Transformaciones entre nodos
│   ├── delta-validate.py      ← Validación de schema v3
│   ├── delta-init-project.py  ← Inicialización de proyectos
│   ├── validate-schema.py     ← Validador de schemas
│   └── verify_symbol.py       ← Verificador de símbolos
│   │
│   ├── _internal/             ← Lógica interna (no invocable directamente)
│   │   ├── advance.py         ← TASK LOOP de implementación
│   │   ├── verification.py    ← Verificación de predicates
│   │   ├── ceremonies.py      ← Ceremonias de scrum
│   │   ├── close.py           ← Lógica de cierre de sprint
│   │   ├── enforce.py         ← Enforcement de reglas
│   │   ├── graph.py           ← Grafo de dependencias
│   │   ├── score.py           ← Score de tareas
│   │   ├── qlty_check.py      ← Quality checks
│   │   ├── deep_analysis.py   ← Análisis profundo
│   │   ├── hallucination.py   ← Detección de alucinaciones
│   │   ├── progress_checksum.py ← Checksum de progress.json
│   │   ├── projects.py        ← Gestión de proyectos
│   │   ├── prompt_engine.py   ← Engine de prompts
│   │   └── ast_grep_utils.py  ← Utilidades de AST
│   │
│   └── tests/                 ← Tests
│       ├── test_basic.py
│       └── test_skill_e2e.py
│
└── templates/                 ← Templates de output
    ├── output-advance.md
    ├── output-closure.md
    └── output-plan.md
```

### 4.2 Arquitectura de capas

```
┌─────────────────────────────────────────────┐
│               LLM (agente)                   │
│  Solo ejecuta: walker.py <comando>           │
├─────────────────────────────────────────────┤
│               walker.py                      │
│  Engine de workflow graph                    │
│  - Lee workflow.json                         │
│  - Persiste .delta-walker.json               │
│  - Ejecuta auto_execute                      │
│  - Valida gates                              │
│  - Evalúa post_gate                          │
│  - Maneja contratos (data_contract, on_exit) │
│  - Auto-migración                            │
│  - Resolve command                           │
├─────────────────────────────────────────────┤
│   spring.py    task.py    delta-gate-check   │
│   (sprints)    (tareas)  (gate validation)   │
├─────────────────────────────────────────────┤
│              _internal/                      │
│  advance.py  verification.py  ceremonies.py  │
│  close.py    enforce.py       graph.py       │
│  score.py    qlty_check.py    ...            │
├─────────────────────────────────────────────┤
│           filesystem + schemas               │
│  plan.json  progress.json  workflow.json     │
└─────────────────────────────────────────────┘
```

### 4.3 API de walker.py (única interfaz del LLM)

| Comando | Propósito | Cuándo usarlo |
|---------|-----------|---------------|
| `status` | Ver estado actual + transiciones disponibles | Siempre que no sepas dónde estás |
| `resolve` | Diagnosticar conflictos y aplicar estrategias | Cuando el workflow parece trabado |
| `transition <gate>` | Avanzar al siguiente nodo | Cuando sepas qué gate usar |
| `goto <state>` | Forzar transición (solo recuperación) | Solo si resolve confirma que no hay alternativa |
| `reset` | Volver a INIT | Para empezar de cero |
| `health` | Diagnosticar inconsistencias | Cuando sospeches corrupción de datos |
| `verify` | Verificar predicates contra código real | En VERIFY o CODE_REVIEW |
| `approve-task` | Aprobar tarea para advance | Cuando PO aprueba |
| `backlog list/move` | Gestionar backlog del proyecto | Para mover tareas fuera del sprint |
| `query task/file/similar` | Buscar información en el plan | Cuando necesites contexto adicional |

### 4.4 Contratos por edge (Contract-First)

Cada transición declara explícitamente:

```json
{
  "from": "USER_GATE", "to": "ADVANCE_TASK", "gate": "proceed",
  "contract": {
    "input": "decision del usuario (--notes opcional)",
    "output": "decision_log.proceed",
    "pre": "usuario confirmo PROCEDER",
    "post": "ADVANCE_TASK se ejecuta automaticamente"
  }
}
```

| Elemento del contrato | Significado |
|-----------------------|-------------|
| `input` | Qué datos espera el edge |
| `output` | Qué datos produce |
| `pre` | Pre-condición que debe cumplirse |
| `post` | Post-condición después de la transición |
| `note` | Nota para el LLM sobre behavior esperado |

### 4.5 Gate validation

| Nodo | Gate validado | Script | Qué verifica |
|------|---------------|--------|-------------|
| CHECK_SPRINTS | `sprint_selected` | `delta-gate-check.py sprint-count` | Que existan sprints |
| SPRINT_ACTIVE | `has_pending` / `all_done` | `delta-gate-check.py pending-count` | Que haya o no tareas pendientes |
| TASK_PENDING | `ready` | `task.py check-readiness` | PO approval + contexto completo |
| TASK_PENDING | `research_needed` | `task.py check-context-gaps` | Gaps de contexto |
| ANALYSIS_GATE | `analysis_ok` | `task.py check-analysis` | Análisis 3-capas completado |
| CLOSE_CHECK | `health_ok` | `delta-gate-check.py predicates-pass` | Tareas completadas con predicates |
| **REPORT_GATE** | **`proceed`** | **`delta-gate-check.py check-dependencies`** | **Dependencias de la tarea resueltas** |

### 4.6 Data contract por nodo

| Nodo | Flags | Almacenamiento |
|------|-------|----------------|
| ANALYSIS_GATE | `--q1 --a1 --q2 --a2 --q3 --a3` | `contract_data.ADVANCE_TASK[task_id]` |
| PO_REVIEW | `--feedback` | `contract_data.TASK_PENDING[task_id]` |
| CODE_REVIEW | `--notes` | `contract_data.VERIFY[task_id]` |
| VERIFY | `--notes` | `contract_data.PO_ACCEPTANCE[task_id]` |
| PO_ACCEPTANCE | `--feedback` | `contract_data.SPRINT_ACTIVE[task_id]` |
| EDIT_PLAN | `--action --objective --result_state --type --domain ...` | Ejecuta `spring.py add-task/update-task/update-goal` |
| **USER_GATE** | **proceed / refine --notes / cancel --reason** | **`decision_log` + `task_states[task_id] = cancelled` (si cancel)** |
| **REPORT_GATE** | **proceed / refine --notes / cancel --reason** | **`decision_log` + `task_states[task_id] = cancelled` (si cancel)** |

### 4.7 Submódulos de walker.py

| Submódulo | Líneas | Propósito |
|-----------|--------|-----------|
| Constantes | 35-110 | Nodos canónicos, edges, configs por defecto |
| Workflow loading | 145-200 | Carga y auto-migración de workflow.json |
| Auto-migración | 245-370 | `_migrate_workflow`: agrega nodos, actualiza configs, deduplica edges |
| Persistencia | 476-580 | `ensure_state`, `save_state`, `_reconstruct_walker_state` |
| Task ID resolution | 612-650 | `_resolve_task_id`: salta tareas done/blocked/rejected |
| Gate validation | 696-723 | `validate_gate`: ejecuta script de validación |
| Auto execution | 725-764 | `execute_auto_action`: corre entry_action si auto_execute |
| Post-gate | 767-792 | `evaluate_post_gate`: evalúa output y auto-transiciona |
| Plan editing | 972-1115 | `_execute_plan_edit`: add-task, update-task, update-goal |
| Cancel logic | inline | `_mark_task_cancelled`: marca tarea como cancelled en progress.json |
| Commands | 1673-2717 | `cmd_status`, `cmd_current`, `cmd_transition`, `cmd_goto`, `cmd_reset`, `cmd_health`, `cmd_verify`, `cmd_approve_task`, `cmd_backlog`, `cmd_query`, **`cmd_resolve`** |

### 4.8 Diagrama de flujo completo

```
INIT → CHECK_SPRINTS → CREATE_SPRINT → SPRINT_ACTIVE → TASK_PENDING
                                                          │
                                             ┌────────────┼────────────┐
                                             ↓            ↓            ↓
                                        PO_REVIEW     RESEARCH    IN_PROGRESS          (rol: Developer)
                                     (PO: alcance)  (auto: 6    ├→ (desde PO_REVIEW)  └→ ANALYSIS_GATE (rol: Analista)
                                      solo negocio)   capas)         ↓                    ↓
                                          │            ↓       ANALYSIS_GATE       ANALYSIS_REVIEW (rol: Tech Lead)
                                          │       (vuelve a   (rol: Analista)            ↓
                                          │       TASK_PENDING    ↓                USER_GATE (rol: User) ← 🛑 DETIENE
                                          │       o PO_REVIEW ANALYSIS_REVIEW          ├→ proceed → ADVANCE_TASK
                                          │       segun origen) (rol: Tech Lead)       │              ├→ DONE_REVIEW → CODE_REVIEW → REPORT_GATE
                                     PO_REVIEW ←── RESEARCH ───      ↓                 │              │                ├→ proceed → VERIFY → PO_ACCEPTANCE
                                   (vuelve con contexto)        USER_GATE              │              │                │              ├→ SPRINT_ACTIVE
                                   gates: po_approved,          (rol: User)            │              │                │              └→ ADVANCE_TASK
                                   po_rejected,                 ← 🛑 DETIENE           │              │                ├→ refine → PO_REVIEW
                                   po_approved_advance,              ↓                  │              │                └→ cancel → SPRINT_ACTIVE
                                   po_needs_context           ADVANCE_TASK             │              └→ REFINE (rol: Diagnosticador)
                                                              ├→ DONE_REVIEW           │                  ├→ ANALYSIS_GATE / RESEARCH / PO_REVIEW
                                                              └→ CODE_REVIEW           │                  └→ ADVANCE_TASK
                                                                   → REPORT_GATE       ├→ refine → PO_REVIEW → ADVANCE_TASK (po_approved_advance)
                                                                   (rol: User)          └→ cancel → SPRINT_ACTIVE
                                                                   ← 🛑 DETIENE

                                  SPRINT_ACTIVE → EDIT_PLAN (editar plan)
                                  SPRINT_ACTIVE → CLOSE_CHECK (auto)
                                      ├→ health_ok → CLOSE_SPRINT
                                      ├→ health_fail → SPRINT_ACTIVE
                                      └→ handle_pending → HANDLE_PENDING (rol: Scrum Master)
                                          ├→ backlog → CLOSE_SPRINT
                                          ├→ cancel_pending → CLOSE_SPRINT
                                          └→ defer → SPRINT_ACTIVE
```

### 4.9 Estrategia de resolución de conflictos

El comando `walker.py resolve` implementa la siguiente estrategia:

```
1. Detectar problema
   ├── Workflow desactualizado? → auto-migrar
   ├── CLOSE_CHECK con pendientes? → sugerir health_fail
   ├── Estado reconstruido? → informar al LLM
   └── Human gates no visitados? → notificar que tareas nuevas los incluirán

2. Aplicar estrategia
   ├── Si hay auto-migración disponible → ejecutarla
   ├── Si hay transición natural → sugerirla
   └── Si goto es unavoidable → documentar razón

3. Generar feedback
   ├── Qué se detectó
   ├── Qué se hizo
   └── Qué debe hacer el LLM a continuación
```

---

## 5. Conclusiones

### 5.1 Logros

1. **Workflow explícito de 22 nodos** con roles, gates y contratos — el LLM
   nunca adivina qué sigue.

2. **Confirmación humana obligatoria** en USER_GATE (antes de implementar) y
   REPORT_GATE (después de implementar) — el usuario decide PROCEED, REFINE
   o CANCEL en cada punto crítico.

3. **Auto-migración** — proyectos existentes se actualizan automáticamente
   a la última versión del workflow sin intervención manual.

4. **Resolve command** — diagnóstico y resolución de conflictos que evita
   que el LLM use `goto` como primera opción.

5. **Contract-First** — cada transición declara input, output, pre y post
   condiciones, haciendo el workflow auto-documentado.

6. **Validación de dependencias** en REPORT_GATE — no se puede proceder si
   hay dependencias sin resolver.

7. **Persistencia completa** — decision_log, contract_data, task_states,
   walker_state — nada se pierde entre sesiones.

### 5.2 Limitaciones conocidas

| Limitación | Impacto | Mitigación |
|-----------|---------|------------|
| `resolve` no puede reparar progress.json corrupto | El LLM debe usar `reset` | `health` command detecta corrupción |
| No hay rollback de transiciones | Una vez avanzado, no se puede deshacer | `goto` permite volver atrás (sin validación) |
| Dependencias solo se validan en REPORT_GATE | USER_GATE no verifica depends_on | Es intencional: USER_GATE es pre-implementación |
| Auto-migración no elimina edges obsoletos | Pueden quedar edges huérfanos | Se mantienen por compatibilidad |

### 5.3 Trabajo futuro

- **Dashboard visual** del workflow con estado de cada nodo
- **Webhook notifications** cuando el workflow se detiene en USER_GATE/REPORT_GATE
- **Rollback automático** de transiciones fallidas
- **Métricas de ciclo** por tarea (tiempo en cada nodo, número de refinamientos)
- **Paralelización** de tareas independientes
- **Plantillas de workflow** por tipo de proyecto (feature, bugfix, refactor)
- **Integración CI/CD** para ejecutar validaciones automáticas en gates
