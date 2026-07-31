# Delta Skill — Scripts

## API Pública (lo único que el LLM invoca)

Usa `spring.py` para el ciclo de vida del sprint y `task.py` para operaciones de tarea:

```bash
# === SPRINT LIFECYCLE ===
python3 scripts/spring.py create <chain_dir> --project-id <id> --goal "..." ...
python3 scripts/spring.py add-task <chain_dir> --task '{"objective":"...", ...}'
python3 scripts/spring.py update-task <chain_dir> <task_id> --field value
python3 scripts/spring.py update-goal <chain_dir> --goal "..."
python3 scripts/spring.py close <chain_dir>
python3 scripts/spring.py list
python3 scripts/spring.py status <chain_dir>

# === TASK OPERATIONS (roles: --act-as po|dev|qa) ===
python3 scripts/task.py advance <chain_dir> <task_id> --act-as dev --confirm      # Developer (default)
python3 scripts/task.py advance <chain_dir> <task_id> --act-as po --human-confirm  # Product Owner
python3 scripts/task.py advance <chain_dir> <task_id> --act-as qa                   # QA Engineer
python3 scripts/task.py advance <chain_dir> <task_id> --force                       # Omite enforcement
python3 scripts/task.py score <chain_dir> [task_id]
python3 scripts/task.py qlty-check <chain_dir> <task_id> --verbose
python3 scripts/task.py qlty-check --dir <ruta> --verbose

# === SETUP / DIAGNÓSTICO ===
python3 scripts/delta-init-project.py --interactive
python3 scripts/validate-schema.py <json_file> <schema_file>
```

---

## API Interna (delegada por spring.py y task.py)

Estos scripts NO se invocan directamente. Son llamados automáticamente:

| Script | Llamado por |
|--------|-------------|
| `delta-advance.py` | `task.py advance` |
| `delta-score.py` | `task.py score`, `task.py advance` |
| `delta-qlty-check.py` | `task.py qlty-check`, `task.py advance` |
| `delta-enforce.py` | `task.py advance`, `spring.py close` |
| `delta-close.py` | `spring.py close` |
| `delta-eval.sh` | `spring.py create` |
| `analyze_refactored.py` | `delta-qlty-check.py` |
| `hallucination-scanner.py` | `delta-qlty-check.py` |
| `projects.py` | `spring.py`, `delta-init-project.py` |

---

## Pipeline Completo

El pipeline se ejecuta a través de 3 comandos del LLM:

### 1. `spring.py create` — Inicializar sprint

```
Entrada:  Usuario da un goal, O no hay delta activo y hay que crear uno
Acción:   spring.py create construye plan.json + progress.json con:
          • FASE 0-2: Contexto + G_objetivo + G_actual
          • FASE 3: Descomposición en tareas
          • FASE 4-5: Riesgos + Score
          • FASE 6-8: Escritura de archivos + validación enforcement
Salida:   <chain_dir>/plan.json + <chain_dir>/progress.json
          Si la validación post-write falla → archivos eliminados, sprint no creado (exit 2)
Verif.:   □ plan.json pasa schema validation? □ progress.json pasa schema validation?
          □ Todas las tareas tienen task_id, objective, result_state, validation.predicates?
          □ project.project_id está definido?
```

```bash
python3 scripts/spring.py create <chain_dir> --project-id <id> \
    --goal "Implementar X" --predicates '{}' --tasks '[]'
```

### 2. `task.py advance` — Avanzar tarea (enforcement + verificación + confirmación)

#### Roles disponibles (`--act-as po|dev|qa`)

| Rol | Flag | Comportamiento | Uso típico |
|-----|------|----------------|------------|
| **Developer** 👨‍💻 | `--act-as dev` | Implementa, ejecuta task loop, puede confirmar (`--confirm`). **NO** puede hacer `--human-confirm`. | Flujo normal de desarrollo. **Valor por defecto.** |
| **Product Owner** 🧑‍💼 | `--act-as po` | Puede hacer `--human-confirm`. En tareas `ready_for_review`, salta enforcement + task loop. **NO** implementa código. | Usuario revisa y aprueba una tarea completada. |
| **QA Engineer** 🔍 | `--act-as qa` | Qlty-check obligatorio (no permite `--skip-qlty`). **NO** puede confirmar ni hacer human-confirm. | Validación de calidad post-implementación. |

**Reglas de los gates:**
1. `--human-confirm` solo funciona con `--act-as po`. Si un Dev/QA lo intenta → error + sugerencia.
2. `--act-as qa` bloquea `--skip-qlty`. QA siempre ejecuta quality gates.
3. Sin `--act-as` → default `dev` (backward compatible).

#### Pipeline completo

```
Entrada:  Tarea implementada, lista para verificar
Acción:   task.py advance integra en un solo comando:
          0. ROLE IDENTITY: banner con rol, nombre, descripción
          1. ROLE GATES: validaciones según --act-as (human-confirm, qlty, ready_for_review)
          2. ENFORCEMENT: delta-enforce (next_action, curvature, distance, backlog)
          3. SCORE: delta-score (FASE 5)
          4. TASK LOOP: delta-advance (loop_verification, acceptance_criteria, quality_gates)
          5. QLTY GATES: delta-qlty-check (8 quality gates automáticos)
          6. CONFIRM: actualiza progress.json
          + Post-write validation enforcement (si falla → bloquea avance)
Salida:   Exit 0 = completada, Exit 1 = partial (REFINE), Exit 2 = error, Exit 3 = ready_for_review
Verif.:   □ Exit code 0? □ trail actualizado? □ next_action movido a siguiente tarea?
```

```bash
# Developer (default): implementa y confirma
python3 scripts/task.py advance <chain_dir> <task_id> --act-as dev --confirm
python3 scripts/task.py advance <chain_dir> <task_id> --act-as dev --confirm --force  # Con override

# Product Owner: revisa y confirma (salta re-evaluación si la tarea está en ready_for_review)
python3 scripts/task.py advance <chain_dir> <task_id> --act-as po --human-confirm

# QA: valida quality gates (qlty obligatorio)
python3 scripts/task.py advance <chain_dir> <task_id> --act-as qa
```

#### Las 4 capas del TASK LOOP

Ejecutadas internamente por `task.py advance`:

1. **loop_verification** — checks grep/pytest contra código real
2. **acceptance_criteria** — criterios de aceptación de alto nivel
3. **quality_gates** — barreras de calidad no negociables (task-defined)
4. **Qlty quality gates** — 8 gates automáticos evaluados por `delta-qlty-check`:

| Gate | Métrica | Umbral |
|------|---------|--------|
| `production_bugs` | eval, pickle, unwrap, mutable defaults, etc. | 0 críticos |
| `bug_probability` | cruces de datos (exception swallow + cyclo alta) | 0 críticos |
| `security_high` | inyecciones B6xx (shell, SQL) | 0 issues |
| `dead_code` | imports/variables sin usar F401/F841 | ≤ 5/file |
| `spaghetti_complexity` | CC por función > 15, LCOM > 20 | 0 |
| `unnecessary_code` | f-strings vacíos, debug leftovers, funciones vacías | ≤ 3/file |
| `hallucinated_imports` | imports que no existen en proyecto/stdlib/paquetes | 0 |
| `code_duplication` | bloques identical/similar code desde qlty smells | ≤ 1/file |

#### Loop Prompting con CPG+Memora

Cuando una tarea alcanza **ready_for_review** (exit 3 de `task.py advance` sin `--confirm`):

```
┌─────────────────────────────────────────────────────┐
│  ready_for_review                                   │
│      ↓                                              │
│  LLM recibe ValidationReport (FeatureTraversal):    │
│    • coverage   — qué símbolos existen / faltan     │
│    • execution  — orden de llamadas correcto        │
│    • contracts  — tipos entre capas válidos         │
│    • gaps       — issues con severity               │
│    • score      — 0.0 a 1.0                        │
│      ↓                                              │
│  LLM autocritica + revisa hipótesis                 │
│    (derive_cgc_hypotheses: dead code, complejidad,  │
│     APIs deprecadas)                                │
│      ↓                                              │
│  LLM consulta contexto histórico                    │
│    (MemoraClient.search: decisiones pasadas sobre   │
│     el mismo dominio/función)                       │
│      ↓                                              │
│  LLM corrige errores detectados                     │
│      ↓                                              │
│  LLM ejecuta task.py advance --act-as dev --confirm o REFINE │
└─────────────────────────────────────────────────────┘
```

Flujo completo de decisión:
`ready_for_review → LLM revisa → LLM autocritica → LLM corrige → task.py advance --act-as dev --confirm`

### 3. `spring.py close` — Cerrar sprint

```
Entrada:  No quedan tareas pendientes (next_action == null, finished == true)
Acción:   Verificación composicional (suma de result_states == G_objetivo)
          + trail final + KV save + post-write validation
Salida:   Plan cerrado, seed para próximo ciclo, candidatos al backlog
Verif.:   □ exit 0 = composición válida? □ exit 1 = revisar gaps?
          □ Backlog actualizado con candidatos?
```

```bash
python3 scripts/spring.py close <chain_dir>
python3 scripts/spring.py close <chain_dir> --force    # Omite validación composicional
python3 scripts/spring.py close <chain_dir> --dry-run  # Simula sin escribir
```

### Comandos auxiliares

```bash
# Refinar sprint en curso (sin crear nuevo)
python3 scripts/spring.py add-task <chain_dir> --task '{"objective": "...", "type": "feature", ...}'
python3 scripts/spring.py update-task <chain_dir> TASK-001 --domain security
python3 scripts/spring.py update-goal <chain_dir> --goal 'Nuevo objetivo refinado'

# Monitoreo
python3 scripts/spring.py list              # Todos los sprints
python3 scripts/spring.py status <chain>    # Dashboard de un sprint

# Score / Code review independiente
python3 scripts/task.py score <chain_dir> [task_id]
python3 scripts/task.py qlty-check <chain_dir> <task_id> --verbose
python3 scripts/task.py qlty-check --dir <ruta> --verbose
```

---

## Modos de invocación (protocolos detallados)

### `--code-review` — Code review con Qlty

Ejecuta `task.py qlty-check --dir <ruta>` y reporta métricas. Solo lectura — no modifica nada.

**Protocolo (fallback chain):**

```
1. ¿Usuario dio una ruta exacta?
   → Úsala literal: python3 scripts/task.py qlty-check --dir <ruta_exacta> --verbose

2. ¿No hay ruta? ¿Hay un delta activo (current.json)?
   → Resolver chain_dir según proyecto activo:
     • Multi-project: .cognitive/projects/<project_id>/delta/<chain_id>/
     • Legacy: .cognitive/delta/<chain_id>/
   → Leer plan.json → juntar impacted_artifacts.files
   → python3 scripts/task.py qlty-check <chain_dir> <alguna_task_id> --verbose

3. ¿No? → Usar git diff del proyecto actual
   → python3 scripts/task.py qlty-check --verbose

4. ¿No hay archivos modificados? → Preguntar al usuario
```

### `--status` — Dashboard

```
1. Si hay delta activo → python3 scripts/spring.py status <chain_dir>
2. Si no → python3 scripts/spring.py list para ver todos los deltas
3. Reportar al usuario sin modificar nada
```

### `--audit` — Auditoría de integridad

```
python3 scripts/spring.py status <chain_dir> --audit
```

Verifica consistencia entre plan.json y progress.json, que todas las tareas completadas
tengan trail entries, y que no haya archivos huérfanos.

---

## Dashboard de Deltas

### Formato

Al inicio de cada sesión (o cuando se pregunte por el estado), el LLM genera un resumen
con tabla: **proyecto** | **delta** | **estado** | **tareas** | **progreso %**

### Estados posibles

| Estado | Significado |
|--------|-------------|
| `active` | Delta en progreso (es el `current.json`) |
| `suspended` | Delta pausado, se retomará después |
| `closed` | Todas las tareas completadas y cerradas |
| `superseded` | Reemplazado por otro delta |

### Ubicaciones escaneadas

- `.cognitive/projects/*/delta/*/progress.json` (multi-project)
- `.cognitive/delta/*/progress.json` (legacy)

Alternativamente: `python3 scripts/spring.py list`

---

## Backlog de Tareas (Fuera de Sprint)

Tareas detectadas que no caben en el sprint actual pero son válidas para futuros sprints.

### Ubicaciones

- **Multi-project**: `.cognitive/projects/<project_id>/delta/backlog.jsonl`
- **Legacy**: `.cognitive/delta/backlog.jsonl`

### Formato (líneas JSON, un item por línea)

```json
{"id": "BL-001", "detected_at": "2026-07-09T18:00:00Z", "source": "cognitive_state",
 "domain": "scrum", "description": "Integrar SprintStateService en el MCP como tool",
 "rationale": "La interfaz se creó pero no tiene tool MCP que la exponga",
 "priority": "medium", "status": "pending"}
```

### Campos

| Campo | Descripción |
|-------|-------------|
| `id` | Identificador único (`BL-<secuencial>`) |
| `detected_at` | ISO timestamp |
| `source` | Origen: `cognitive_state`, `code-analyze`, `code-lookup`, `review`, `user`, `spring.py close` |
| `domain` | Bounded context |
| `description` | Descripción de la tarea |
| `rationale` | Por qué se detectó (opcional) |
| `priority` | `critical`, `high`, `medium`, `low` |
| `status` | `pending`, `picked`, `discarded` |

### Ciclo de vida

```
detected → pending → picked → spring.py create → closed
                       → discarded (con rationale)
```

### Reglas

1. **No interrumpe el sprint activo.** Se escribe al backlog sin cambiar `current.json`.
2. El LLM lo revisa al iniciar un nuevo delta: antes de `spring.py create`, chequea items `pending`.
3. Al cerrar un delta, revisar si el gap composicional debe ir al backlog.
4. **No duplicados**: si un item con misma `description` y `status != discarded` ya existe, no se agrega.

---

## Recovery (qué hacer cuando algo falla)

| Síntoma | Causa probable | Acción del LLM |
|---|---|---|
| `spring.py create` exit 2 (ENFORCEMENT BLOCKED) | Schema validation falló post-write | Leer el output del script — lista cada error con `path`, `message` y `valor actual`. Corregir campos en el JSON y re-ejecutar create. Los archivos sucios ya fueron eliminados. |
| `task.py advance` exit 2 (ENFORCEMENT BLOCKED) | Schema validation falló en progress.json post-escritura | Error interno del script. Reportar al usuario: "Error de validación interna en task.py advance. El progreso NO se guardó." Revisar progress.json manualmente. |
| `task.py advance` exit 1 | TASK LOOP partial (no alcanzó result_state) | Leer el output del script — contiene la evaluación de qué predicates fallaron, con grep/pytest output. Refinar el código según el fallo. |
| `spring.py close` exit 1 | Composición parcial o side-effects | Leer el output — lista qué predicates no se satisfacen. Crear tareas para resolverlos o agregar al backlog. |
| `spring.py close` exit 2 (ENFORCEMENT BLOCKED) | Schema validation falló en datos de cierre | Datos de cierre no se guardaron. Revisar y reintentar. |
| Schema validation falla | Predicates mal formados, task_ids duplicados, campos faltantes | Usar `validate-schema.py` directamente para diagnóstico: `python3 scripts/validate-schema.py <file> schema/plan.schema.json`. |
| Un script no se encuentra | Script no registrado en `tools.json` | Registrarlo en `tools.json` con task_types, usage, exit_codes, y validar: `python3 scripts/validate-schema.py tools.json schema/tools.schema.json` |
| El output del script no es claro | El script no implementa self-sufficient output | Re-ejecutar con `--verbose`. Si aún no es claro, el script necesita implementar self-sufficient (marcar `self_sufficient: true` en tools.json). |

---

## Initialización de proyecto

Antes de crear sprints, el proyecto debe estar registrado con `delta-init-project.py`.

```bash
python3 scripts/delta-init-project.py \
    --project-id "mi-proyecto" \
    --name "Mi Proyecto" \
    --description "Descripción breve" \
    --repo-path "/ruta/al/repo" \
    --language "python" \
    --framework "django 5.x" \
    --patterns "hexagonal, ddd" \
    --bounded-contexts "users, payments, games"
```

**Todos los campos son obligatorios** — strings vacíos producen exit code 2.

El script crea:
1. `.cognitive/projects.json` (registro global)
2. `.cognitive/projects/<project_id>/project.json` (perfil del proyecto)
3. `.cognitive/projects/<project_id>/delta/` (directorio para sprints)

---

## Self-Sufficient Output Pattern

Cada script DEBE producir en su **stdout** las instrucciones que el LLM necesita
para decidir el siguiente paso. **El LLM no debe necesitar consultar el SKILL.md**
para saber qué hacer con un exit code.

### Reglas del patrón:

1. **exit 0** → output debe decir "todo ok" y dar el siguiente comando
2. **exit 1** → output debe decir qué falló, cómo arreglarlo, y el comando para reintentar
3. **exit 3** (delta-advance) → output debe incluir la evaluación (coverage, gaps, QGates) y las opciones de decisión con comandos concretos
4. Marcar `self_sufficient: true` en `tools.json` cuando el script implemente esto

---

## Registro en tools.json

Cada script aquí debe tener una entrada en `tools.json` con:

- `task_types`: qué tipos de tarea aplican
- `work_types`: qué subtipos aplican
- `usage`: comando de uso con placeholders
- `exit_codes`: significado de cada código de salida
- `when_to_run`: cuándo debe ejecutar esta tool el agente
- `self_sufficient: true` si implementa el patrón self-sufficient
- `depends_on`: dependencias del script

Los scripts etiquetados como `⚠️ INTERNAL` son de uso interno delegado por
`spring.py` y `task.py` — no se invocan directamente.

### Verificar sincronización

```bash
# Validar tools.json contra su schema
python3 scripts/validate-schema.py tools.json schema/tools.schema.json

# Verificar que todos los .py tienen entrada
python3 -c "
import json, os
reg = {t['name'] for t in json.load(open('tools.json')).get('tools', [])}
ok = True
for f in os.listdir('scripts/'):
    if f.endswith('.py') and f != '__init__.py' and f[:-3] not in reg:
        print(f'❌ {f} sin entrada en tools.json')
        ok = False
if ok:
    print('✅ Todos los scripts registrados')
"
```

---

## Schemas

| Schema | Archivo | Propósito |
|--------|---------|-----------|
| plan.schema.json | `schema/plan.schema.json` | Estructura de plan.json (task types, work types) |
| progress.schema.json | `schema/progress.schema.json` | Estructura de progress.json |
| tools.schema.json | `schema/tools.schema.json` | Schema del registro de tools |
| project.schema.json | `schema/project.schema.json` | Schema del perfil de proyecto |

También: `tools.json` en la raíz del skill es una instancia de `tools.schema.json`.

---

## Templates

- `templates/output-plan.md` — formato para /delta
- `templates/output-advance.md` — formato para ADVANCE (achieved/partial)
- `templates/output-closure.md` — formato para CLOSURE

---

## analyze_refactored.py

Es el motor Qlty. No se invoca directamente — lo llama `delta-qlty-check.py`. Ejecuta:

- `qlty metrics` (CC, NLOC, LCOM, cognitive complexity)
- `qlty smells` (duplicación, code smells)
- `qlty check` (ruff, eslint, bandit, clippy...)
- `ast-grep deep analysis` (clasifica hallazgos)

Requiere `qlty` y `sg` (ast-grep) instalados.

---

## CI automático

El workflow `.github/workflows/delta-tools-sync.yml` ejecuta ambas validaciones
en cada PR que modifique archivos en `scripts/` o `tools.json`.

Si agregas un script sin registrarlo en `tools.json`:
- ❌ Fallará el CI
- ❌ El agente no sabrá cuándo ejecutarlo
