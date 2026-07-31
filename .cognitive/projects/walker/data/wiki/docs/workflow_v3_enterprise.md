# Workflow Engine v3 — Diseño Enterprise

> **Versión:** 3.0-enterprise  
> **Base:** workflow_v3_design.md  
> **Estado:** Diseño refinado con patrones enterprise para motores de workflow LLM-first  
> **Principios:** DDD, CQRS, Event-Driven, Saga Pattern, Resilience

---

## Tabla de Contenidos

1. [Gaps del diseño v2 → v3](#1-gaps-del-diseño-v2--v3)
2. [Taxonomía de ejecución refinada](#2-taxonomía-de-ejecución-refinada)
3. [Arquitectura en capas](#3-arquitectura-en-capas)
4. [Modelo de dominio](#4-modelo-de-dominio)
5. [TaskRegistry y tareas puras](#5-taskregistry-y-tareas-puras)
6. [SideEffect Engine con resiliencia](#6-sideeffect-engine-con-resiliencia)
7. [API Surface definitiva](#7-api-surface-definitiva)
8. [Ciclo de vida de sesión](#8-ciclo-de-vida-de-sesión)
9. [Contract Enforcement](#9-contract-enforcement)
10. [Observabilidad enterprise](#10-observabilidad-enterprise)
11. [Error Taxonomy y Recovery](#11-error-taxonomy-y-recovery)
12. [Esquema workflow.json v3](#12-esquema-workflowjson-v3)
13. [Roadmap de migración v2 → v3](#13-roadmap-de-migración-v2--v3)

---

## 1. Gaps del diseño v2 → v3

### 1.1 Lo que v2 ya tiene bien

| Acierto v2 | Por qué |
|-----------|---------|
| DDD puro: domain/application/infrastructure | Separación de responsabilidades correcta |
| domain/models.py con dataclasses | Modelos inmutables, sin IO |
| Use cases con SRP | Cada operación es una unidad atómica |
| Eventos de dominio | DomainEvent + DomainEventType |
| Presenter/loader separados | UI e IO desacoplados |

### 1.2 Gaps identificados

| Gap | Impacto | Solución v3 |
|-----|---------|-------------|
| Solo 2 modos (auto/manual) | No diferencia LLM_task de user_input ni bootstrap | 5 ExecutionTypes con contratos formales |
| Sin TaskRegistry | Acciones hardcodeadas en scripts | Registry con tasks puras registrables |
| Sin SideEffectEngine | Side-effects embebidos en use cases sin transaccionalidad | Engine dedicado con retry + compensation |
| Sin TaskResult | Cada action retorna dict inconsistente | `TaskResult[T]` genérico tipado |
| Sin contexto de ejecución | No hay trace_id, metadata, baggage | `ExecutionContext` obligatorio en toda llamada |
| Sin observabilidad | No hay métricas, tracing, audit estructurado | Capa de observabilidad cross-cutting |
| Sin sesión formal | session_id es un string sin lifecycle | SessionManager con estados explícitos |
| Sin contract enforcement | Pre/post condiciones son strings sin validar | ContractValidator con schemas JSON |
| Sin recovery | Si el engine falla, no hay replay automático | Checkpoint + Saga + Automatic Recovery |
| Sin error taxonomy | ValidationError para todo | Jerarquía de errores tipados |
| API acoplada a CLI | commands.py mezcla CLI + orchestación | API surface limpia: step/submit/status/transition |
| Sin idempotencia | Mismo submit puede ejecutarse 2 veces | IdempotencyKey en cada operación |
| Sin versioning de workflow | Un solo workflow.json sin historial | SchemaRegistry + migración automática |

---

## 2. Taxonomía de ejecución refinada

### 2.1 Los 5 ExecutionTypes

Cada sub-estado tiene un `execution` que define **quién ejecuta**, **quién decide el siguiente paso**, y **qué contrato aplica**:

```
┌──────────────────────────────────────────────────────────────────┐
│                    ExecutionType Taxonomy                         │
├────────────────┬─────────────────┬──────────────────┬────────────┤
│  Type           │ Ejecuta         │ Decide next       │ Contrato   │
├────────────────┼─────────────────┼──────────────────┼────────────┤
│  bootstrap     │ Engine          │ Engine + Data     │ session_id  │
│  auto          │ Engine          │ Engine (result)   │ TaskResult  │
│  llm_task      │ LLM externo     │ Engine (schema)   │ prompt→JSON │
│  user_input    │ Usuario humano  │ Engine (options)  │ options→sel │
│  manual        │ Agente/LLM      │ Agente (submit)   │ required_flds│
└────────────────┴─────────────────┴──────────────────┴────────────┘
```

### 2.2 Lifecycle de ejecución

Cada ejecución (sea del tipo que sea) sigue este ciclo:

```
  ┌──────────┐
  │ on_entry │  → Mensaje/meta para el ejecutor
  └────┬─────┘
       ↓
  ┌──────────┐
  │ execute  │  → La acción en sí (TaskRegistry, prompt, options, script)
  └────┬─────┘
       ↓
  ┌──────────┐
  │ validate │  → Validar output contra output_schema
  └────┬─────┘
       ↓
  ┌──────────┐
  │ on_error │  → Si falla: retry? on_failure? log?
  └────┬─────┘
       ↓
  ┌──────────┐
  │ on_exit  │  → Side-effects post-ejecución
  └──────────┘
```

### 2.3 Contratos por ExecutionType

#### `bootstrap`

```json
{
  "execution": "bootstrap",
  "action": "session_init",
  "output_schema": {
    "session_id": "string",
    "project_id": "string (optional)"
  }
}
```

- Engine asigna `session_id` auto-generado si no existe
- Detecta proyecto/sprint/tarea existente
- Si no hay: pide al LLM crear proyecto y sprint
- Ejecuta `on_entry` → Engine decide automáticamente

#### `auto`

```json
{
  "key": "CODE_REVIEW_SCAN",
  "execution": "auto",
  "action": "review_code",
  "input": { "artefactos": ["..."] },
  "output_schema": {
    "passed": "boolean",
    "findings": "array",
    "summary": "string"
  },
  "on_success": "KNOWLEDGE_UPDATE",
  "on_failure": "REPORT_GATE",
  "side_effects": [
    {"type": "update_cpg", "params": {"paths": "artefactos_impactados"}}
  ],
  "retry": { "max_attempts": 3, "backoff": "exponential", "initial_delay_ms": 1000 }
}
```

- Engine ejecuta `TaskRegistry.execute("review_code", ctx)` 
- Resultado se valida contra `output_schema`
- Side-effects se ejecutan después
- `on_success` / `on_failure` deciden el siguiente sub-estado
- El LLM **nunca ve** este paso a menos que `on_failure` lo requiera

#### `llm_task`

```json
{
  "key": "ANALYZE",
  "execution": "llm_task",
  "prompt": "Analiza 6→3 capas usando: {context}",
  "prompt_template": "string",
  "input_schema": {
    "cpg_context": "object",
    "ckg_context": "object"
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "q1": {"type": "string"},
      "a1": {"type": "string"},
      "q2": {"type": "string"},
      "a2": {"type": "string"},
      "q3": {"type": "string"},
      "a3": {"type": "string"}
    },
    "required": ["q1", "a1", "q2", "a2", "q3", "a3"]
  },
  "timeout_ms": 120000,
  "retry": { "max_attempts": 2, "backoff": "fixed", "initial_delay_ms": 5000 }
}
```

- API devuelve `{execution: "llm_task", prompt: "...", output_schema: {...}}`
- LLM responde con `submit(session_id, data)` donde data debe cumplir `output_schema`
- Engine valida, si no cumple → `on_failure` o retry
- Si cumple → `on_success`

#### `user_input`

```json
{
  "key": "USER_GATE",
  "execution": "user_input",
  "user_message": "INFORME PRE-IMPLEMENTACION...",
  "options": [
    {"value": "proceed", "label": "Proceder", "description": "Implementar la tarea"},
    {"value": "refine", "label": "Refinar", "requires": ["notes"]},
    {"value": "cancel", "label": "Cancelar", "requires": ["reason"]}
  ],
  "on_success": "WORK",
  "on_failure": "SPRINT"
}
```

- API devuelve `{execution: "user_input", user_message: "...", options: [...]}`
- LLM presenta al usuario, recibe elección, traduce a `submit(session_id, {decision: "proceed"})`
- Engine valida contra options, decide siguiente paso

#### `manual`

```json
{
  "key": "KNOWLEDGE_UPDATE",
  "execution": "manual",
  "action": "collect_knowledge",
  "required_fields": ["actualizaciones", "nuevas_reglas"],
  "input_schema": {
    "actualizaciones": {"type": "array"},
    "nuevas_reglas": {"type": "array", "items": {"type": "object"}},
    "nuevas_advertencias": {"type": "array", "items": {"type": "object"}}
  },
  "timeout_ms": 300000
}
```

- API devuelve `{execution: "manual", required_fields: [...], input_schema: {...}}`
- El agente/LLM ejecuta pasos externos (leer archivos, consultar APIs)
- Cuando completa, llama `submit(session_id, data)` con los campos requeridos
- Engine valida contra `input_schema`

---

## 3. Arquitectura en capas

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           API LAYER                                      │
│  step() | submit() | status() | transition() | session() | health()     │
│  Formato: REST-like sobre JSON-RPC                                        │
│  SRP: Unica superficie de contacto para LLM/agentes                      │
├──────────────────────────────────────────────────────────────────────────┤
│                        APPLICATION LAYER                                 │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────────────┐  │
│  │ Orchestration    │  │ Contract          │  │ SessionManager         │  │
│  │ Service          │  │ Enforcement       │  │ - init()               │  │
│  │ - step()         │  │ - validate_input  │  │ - resume()             │  │
│  │ - submit()       │  │ - validate_output │  │ - suspend()            │  │
│  │ - transition()   │  │ - check_pre/post  │  │ - terminate()          │  │
│  └─────────────────┘  └──────────────────┘  └────────────────────────┘  │
├──────────────────────────────────────────────────────────────────────────┤
│                         DOMAIN LAYER                                     │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────────────┐  │
│  │ WorkflowGraph    │  │ SprintState       │  │ Domain Events          │  │
│  │ - Nodes          │  │ - SubStateData    │  │ - StateTransitioned    │  │
│  │ - Edges          │  │ - History         │  │ - DataSubmitted        │  │
│  │ - Gates          │  │ - Context         │  │ - TaskCompleted        │  │
│  └─────────────────┘  └──────────────────┘  └────────────────────────┘  │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────────────┐  │
│  │ ExecutionType    │  │ TaskResult<T>     │  │ SideEffect             │  │
│  │ - bootstrap      │  │ - ok: bool        │  │ - type: str            │  │
│  │ - auto           │  │ - data: T         │  │ - params: dict         │  │
│  │ - llm_task       │  │ - error: Error    │  │ - idempotency_key      │  │
│  │ - user_input     │  │ - metrics: dict   │  │ - compensation: fn     │  │
│  │ - manual         │  │ - side_effects[]  │  └────────────────────────┘  │
│  └─────────────────┘  └──────────────────┘                               │
├──────────────────────────────────────────────────────────────────────────┤
│                       INFRASTRUCTURE LAYER                                │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────────────┐  │
│  │ TaskRegistry     │  │ SideEffectEngine  │  │ SessionStore           │  │
│  │ - register()     │  │ - execute()       │  │ - load()               │  │
│  │ - execute()      │  │ - compensate()    │  │ - save()               │  │
│  │ - list()         │  │ - retry()         │  │ - delete()             │  │
│  └─────────────────┘  └──────────────────┘  └────────────────────────┘  │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────────────┐  │
│  │ ScriptRunner     │  │ EventStore        │  │ WorkflowRepository     │  │
│  │ (v2 legacy)      │  │ - append()        │  │ - load()               │  │
│  │                  │  │ - replay()        │  │ - resolve_path()       │  │
│  └─────────────────┘  └──────────────────┘  └────────────────────────┘  │
├──────────────────────────────────────────────────────────────────────────┤
│                     CROSS-CUTTING CONCERNS                                │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────────────┐  │
│  │ Observability    │  │ Error Handler     │  │ Resilience             │  │
│  │ - Logger         │  │ - ErrorRegistry   │  │ - RetryPolicy          │  │
│  │ - Metrics        │  │ - ErrorClassifier │  │ - CircuitBreaker       │  │
│  │ - Tracer         │  │ - RecoveryPlan    │  │ - Checkpoint           │  │
│  └─────────────────┘  └──────────────────┘  └────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Modelo de dominio

### 4.1 ExecutionType (nuevo)

```python
class ExecutionType(str, Enum):
    """Modo de ejecución de un sub-estado.
    
    Define QUIÉN ejecuta la acción y QUIÉN decide el siguiente paso.
    """
    BOOTSTRAP = "bootstrap"      # Engine asigna session, detecta contexto
    AUTO = "auto"                # Engine ejecuta TaskRegistry, decide
    LLM_TASK = "llm_task"        # LLM recibe prompt, engine valida schema
    USER_INPUT = "user_input"    # Usuario elige opción, LLM traduce
    MANUAL = "manual"            # Agente ejecuta pasos externos, LLM submit
```

### 4.2 SubState extendido (v3)

```python
@dataclass
class SubState:
    """Paso dentro de un nodo del workflow. Versión enterprise."""
    key: str
    label: str = ""
    execution: ExecutionType = ExecutionType.MANUAL
    action: str = ""                       # Nombre en TaskRegistry (para auto/manual)
    
    # Contrato de entrada/salida
    required_fields: list[str] = field(default_factory=list)
    input_schema: dict = field(default_factory=dict)    # JSON Schema de entrada
    output_schema: dict = field(default_factory=dict)   # JSON Schema de salida
    
    # Prompting (para llm_task)
    prompt: str = ""
    prompt_template: str = ""              # Template con {placeholders}
    
    # Interacción humana (para user_input)
    user_message: str = ""
    options: list[SelectOption] = field(default_factory=list)
    
    # Flujo
    on_success: str = ""                   # Siguiente sub-estado
    on_failure: str = ""                   # Sub-estado alternativo
    on_failure_loop: bool = False          # Hacia atrás (legacy compat)
    
    # Side-effects
    side_effects: list[SideEffectDef] = field(default_factory=list)
    
    # Resiliencia
    retry: RetryPolicy | None = None
    timeout_ms: int = 120000
    
    # Mensajes
    on_entry_message: str = ""
    
    # Meta
    order: int = 0
    version: str = "3.0"


@dataclass
class SelectOption:
    """Opción para execution=user_input."""
    value: str
    label: str = ""
    description: str = ""
    requires: list[str] = field(default_factory=list)  # Campos adicionales si se elige esta


@dataclass
class RetryPolicy:
    """Política de reintento para ejecución."""
    max_attempts: int = 3
    backoff: str = "exponential"  # fixed | linear | exponential | jitter
    initial_delay_ms: int = 1000
    max_delay_ms: int = 60000


@dataclass
class SideEffectDef:
    """Definición declarativa de un side-effect."""
    type: str
    params: dict = field(default_factory=dict)
    optional: bool = False       # Si True, fallo no bloquea
    idempotency_key: str = ""   # Para evitar duplicados
```

### 4.3 TaskResult (genérico tipado)

```python
T = TypeVar("T")

@dataclass
class TaskResult(Generic[T]):
    """Resultado tipado de una tarea pura.
    
    T es el tipo del data esperado (definido por output_schema).
    """
    ok: bool
    data: T | None = None
    error: WorkflowError | None = None
    side_effects: list[SideEffectDef] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)  # Tiempo, hits, etc.
    trace_id: str = ""
    
    @classmethod
    def success(cls, data: T, **kw) -> TaskResult[T]:
        return cls(ok=True, data=data, **kw)
    
    @classmethod 
    def failure(cls, error: WorkflowError, **kw) -> TaskResult[T]:
        return cls(ok=False, error=error, **kw)
```

### 4.4 ExecutionContext

```python
@dataclass
class ExecutionContext:
    """Contexto de ejecución para toda operación del engine.
    
    SRP: Transportar metadata de ejecución sin acoplarse al dominio.
    Todo código en domain/ recibe este contexto.
    """
    session_id: str
    trace_id: str                          # Para tracing distribuido
    span_id: str                           # Para spans individuales
    chain_dir: str
    task_id: str = ""
    workflow_version: str = "3.0"
    
    # Baggage para transportar datos entre capas sin modificar dominio
    baggage: dict[str, Any] = field(default_factory=dict)
    
    # Metadata de ejecución
    started_at: str = ""
    previous_node: str = ""
    previous_sub_state: str = ""
    
    @classmethod
    def create(cls, session_id: str, chain_dir: str, **kw) -> ExecutionContext:
        return cls(
            session_id=session_id,
            trace_id=kw.pop("trace_id", uuid4().hex[:12]),
            span_id=kw.pop("span_id", uuid4().hex[:8]),
            chain_dir=chain_dir,
            **kw,
        )
```

---

## 5. TaskRegistry y tareas puras

### 5.1 Contrato

```python
class TaskRegistry(ABC):
    """Registry de tareas puras.
    
    Una tarea pura: recibe ExecutionContext + dict → retorna TaskResult[T]
    No hace IO directa — los side-effects se declaran en el resultado.
    """

    @abstractmethod
    def register(self, name: str, fn: Callable) -> None: ...
    
    @abstractmethod
    def execute(self, name: str, ctx: ExecutionContext, input_data: dict) -> TaskResult: ...
    
    @abstractmethod
    def list_tasks(self) -> list[str]: ...
    
    @abstractmethod
    def get_task(self, name: str) -> Callable | None: ...
```

### 5.2 Tareas registrables (catálogo)

| Nombre | Input | Output | Descripción |
|--------|-------|--------|-------------|
| `session_init` | nada | `{session_id, project?}` | Inicializar sesión |
| `gather_context` | `{artefactos}` | `{cpg, context}` | Recopilar contexto CPG |
| `analyze_capas` | `{context}` | `{q1,a1,q2,a2,q3,a3}` | Análisis 6→3 capas |
| `review_analysis` | `{analysis}` | `{passed, findings}` | Revisar análisis |
| `implement_task` | `{task}` | `{files_modified}` | Implementar tarea |
| `review_code` | `{artefactos}` | `{passed, findings}` | Code review automático |
| `run_tests` | `{paths}` | `{passed, report}` | Ejecutar tests |
| `close_task` | `{task_id}` | `{closed}` | Cerrar tarea |
| `save_to_ckg` | `{nodes}` | `{saved}` | Persistir en CKG |
| `route_refinement` | `{target}` | `{routed}` | Enrutar refinamiento |
| `collect_knowledge` | `{}` | `{artefactos, reglas}` | Capturar conocimiento |

### 5.3 Ejemplo de tarea pura

```python
def review_code(ctx: ExecutionContext, input_data: dict) -> TaskResult:
    files = input_data.get("artefactos", [])
    findings = run_static_analysis(files)
    cpg = build_cpg(files)
    
    return TaskResult.success(
        data={
            "passed": len(findings) == 0,
            "findings": findings,
            "summary": f"{len(findings)} hallazgos en {len(files)} archivos",
        },
        side_effects=[
            SideEffectDef(type="update_cpg", params={"paths": files}),
        ],
        metrics={
            "files_analyzed": len(files),
            "execution_ms": elapsed,
        },
    )

# Registro
registry = InMemoryTaskRegistry()
registry.register("review_code", review_code)
registry.register("gather_context", gather_context)
```

### 5.4 Implementación Concreta (este sprint)

#### 5.4.1 Arquitectura del TaskRegistry

```
┌──────────────────────────────────────────────────────────────┐
│                    TaskRegistry (interfaz)                     │
│  domain/registry.py                                            │
│  register(name, fn, schema_in, schema_out) → None              │
│  execute(name, ctx, input_data) → TaskResult                   │
│  list_tasks() → list[str]                                      │
│  get_task(name) → Callable | None                              │
├──────────────────────────────────────────────────────────────┤
│                    InMemoryTaskRegistry                         │
│  infrastructure/task_registry.py                                │
│  - Dict[str, TaskHandler] interno                              │
│  - Ejecuta la función con validación de schemas                │
│  - Mide duración, captura errores, devuelve TaskResult         │
├──────────────────────────────────────────────────────────────┤
│                    Catálogo de Tareas (catalog.py)              │
│  infrastructure/catalog.py                                     │
│  - Registra todas las tareas del workflow.v3.json              │
│  - Cada tarea es una función pura: (ctx, input) → TaskResult   │
│  - No importa otros módulos directamente                       │
│  - Los side-effects se declaran en el resultado                │
└──────────────────────────────────────────────────────────────┘
```

#### 5.4.2 Contrato de handler

```python
@dataclass
class TaskHandler:
    """Handler registrado en el TaskRegistry."""
    name: str
    fn: Callable[[ExecutionContext, dict[str, Any]], TaskResult]
    description: str = ""
    input_schema: dict = field(default_factory=dict)   # JSON Schema
    output_schema: dict = field(default_factory=dict)  # JSON Schema
    timeout_ms: int = 120000
```

#### 5.4.3 Pipeline de ejecución

```
TaskRegistry.execute(name, ctx, input)
  │
  ├── 1. Buscar handler por nombre
  │     └── Si no existe → TaskNotFoundError
  │
  ├── 2. Validar input contra input_schema (ContractValidator)
  │     └── Si no válido → SchemaViolation
  │
  ├── 3. Ejecutar handler.fn(ctx, input) con timeout
  │     ├── Éxito → TaskResult.success(data)
  │     └── Error/fallo → TaskResult.failure(error)
  │
  ├── 4. Validar output contra output_schema
  │     └── Si no válido → SchemaViolation (se registra, no bloquea)
  │
  └── 5. Ejecutar side_effects declarados en el resultado
        └── SideEffectEngine.execute(result.side_effects)
```

#### 5.4.4 Conexión con CPG y CKG (lo que ya existe)

```
walk_v3.py / engine
    │
    ├── TaskRegistry.execute("gather_context", ctx, {task_id})
    │     └── handler llama a CpgQueryService + CkgQueryService
    │         └── NO importa directamente → TaskRegistry media
    │
    ├── TaskRegistry.execute("cpg_grep", ctx, {query})
    │     └── handler llama a CpgQueryService.grep()
    │         └── NO llama a commands.py → llama al servicio puro
    │
    ├── TaskRegistry.execute("ckg_query", ctx, {query})
    │     └── handler llama a CkgQueryService.query()
    │
    └── TaskRegistry.execute("close_sprint", ctx, {})
          └── handler llama a spring.py como script externo
              └── ScriptRunner.run() → side-effect type "run_script"
```

**Regla clave:** Los handlers NO importan otros módulos del proyecto. 
Acceden a servicios (CPG, CKG) a través de inyección de dependencias en el `context`.

#### 5.4.5 ExecutionContext extendido para TaskRegistry

```python
class ExecutionContext:
    session_id: str
    chain_dir: str
    task_id: str
    trace_id: str
    
    # Servicios inyectados (sin import directa)
    cpg_service: CpgQueryService | None = None
    ckg_service: CkgQueryService | None = None
    script_runner: ScriptRunner | None = None
    state_repo: WalkerStateRepository | None = None
    
    # Baggage para datos entre handlers
    baggage: dict[str, Any] = field(default_factory=dict)
```

### 5.5 Catálogo de Migración v2 → v3

Las 17 funciones `cmd_*` de `commands.py` (1365 líneas) se migran a tareas registrables:

#### Fase 1 — Acciones core del workflow (del workflow.v3.json)

| Acción | Origen (v2) | Handler v3 | Side-effects |
|--------|-------------|-----------|-------------|
| `session_init` | `_init_script` | `session_init()` | init session |
| `list_sprints` | `cmd_status` (global) | `list_sprints()` | — |
| `show_dashboard` | `cmd_status` | `show_dashboard()` | — |
| `validate_close` | `spring.py health` | `validate_close()` | — |
| `close_sprint` | `spring.py close` | `close_sprint_v3()` | archive, emit |
| `gather_context` | CPG + task.research | `gather_context_v3()` | update_cpg |
| `gather_task_context` | task.research | `gather_task_context_v3()` | update_cpg |
| `implement_task` | task.advance | `implement_task_v3()` | update_cpg |
| `review_code` | code_review_scan.py | `review_code_v3()` | update_cpg |
| `run_tests` | pytest | `run_tests_v3()` | — |
| `save_to_ckg` | CKG API | `save_to_ckg_v3()` | update_delta |
| `route_to_target` | — | `route_to_target_v3()` | — |
| `validate_plan` | — | `validate_plan_v3()` | — |

#### Fase 2 — Comandos de consulta CPG/CKG

| Comando v2 | Handler v3 | Descripción |
|-----------|-----------|-------------|
| `cmd_cpg_grep` | `cpg_grep()` | grep sobre CPG |
| `cmd_cpg_read` | `cpg_read()` | read archivo via CPG |
| `cmd_cpg_build` | `cpg_build()` | reconstruir CPG |
| `cmd_ckg_query` | `ckg_query()` | query CKG |
| `cmd_deltagraph` | `deltagraph()` | contexto federado |

#### Fase 3 — Comandos de navegación y estado

| Comando v2 | Handler v3 | Descripción |
|-----------|-----------|-------------|
| `cmd_current` | `current_state()` | estado detallado |
| `cmd_resume` | `resume_session()` | reanudar sesión |
| `cmd_goto` | `goto_state()` | ir a sub-estado |
| `cmd_reset` | `reset_state()` | reiniciar estado |
| `cmd_health` | `health_check()` | health del sprint |
| `cmd_query` | `query_workflow()` | query del workflow |
| `cmd_backlog` | `backlog_manager()` | gestión backlog |
| `cmd_verify` | `verify_predicates()` | verificar predicates |
| `cmd_approve_task` | `approve_task_v3()` | aprobar tarea |

### 5.6 Resumen de migración

```
commands.py (1365 líneas, 17 comandos, acoplado)
  │
  └──→ TaskRegistry (6 handlers core + 5 CPG/CKG + 8 navegación)
        │
        ├── Cada handler: ~20-80 líneas (vs 50-200 en v2)
        ├── Sin imports entre handlers
        ├── Side-effects declarativos
        ├── Schemas de entrada/salida explícitos
        └── Registro por nombre → workflow.v3.json dicta el orden
```

**Beneficio:** agregar una nueva tarea = `registry.register("mi_accion", handler_fn)`. 
No tocar `commands.py`, no modificar `walker_v3.py`, no importar otros scripts.

---

## 6. SideEffect Engine con resiliencia

### 6.1 Arquitectura

```
┌─────────────────────────────────────────────────────────────────┐
│                      SideEffectEngine                            │
├─────────────────────────────────────────────────────────────────┤
│  execute(effects: list[SideEffectDef]) → list[SideEffectResult] │
│  compensate(results: list[SideEffectResult]) → None             │
│                                                                 │
│  ┌────────────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │ Handler     │  │ Retry        │  │ Compensator             │  │
│  │ Registry    │  │ Manager      │  │ Registry                │  │
│  └────────────┘  └──────────────┘  └────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 6.2 Principios de ejecución

1. **Transactional**: o todos los side-effects se ejecutan, o todos se compensan
2. **Idempotente**: cada efecto tiene `idempotency_key` → ejecutar N veces = mismo resultado
3. **Retryable**: cada efecto tiene política de reintento (por defecto: 3 attempts, exponential backoff)
4. **Compensable**: cada efecto puede tener un compensador para rollback

### 6.3 Side-Effects del catálogo

| Tipo | Descripción | Idempotente | Compensable |
|------|-------------|-------------|-------------|
| `update_cpg` | Actualizar CPG | ✅ (por paths) | ❌ (no necesario) |
| `save_ckg` | Guardar en CKG | ✅ (por node_id) | ✅ (delete node) |
| `update_delta_graph` | Reconstruir DeltaGraph | ✅ | ❌ |
| `emit_event` | Emitir evento de dominio | ✅ (por event_id) | ✅ (delete event) |
| `persist_snapshot` | Snapshot del estado | ✅ (por timestamp) | ❌ |
| `run_script` | Ejecutar script arbitrario | ❌ (no garantizado) | ❌ |
| `notify` | Notificar al usuario | ✅ (por notification_id) | ❌ |

### 6.4 Ejemplo de ejecución

```python
# El use case ejecuta side-effects después de la tarea pura
effects = [
    SideEffectDef(type="update_cpg", params={"paths": ["x.py"]}),
    SideEffectDef(type="save_ckg", params={"nodes": ["decision-042"]}),
    SideEffectDef(type="emit_event", params={"name": "task_created"}),
]

results = side_effect_engine.execute(ctx, effects)
if any(r.failed for r in results):
    # Rollback: compensar los que ya se ejecutaron
    side_effect_engine.compensate(ctx, results)
    raise SideEffectError("Side-effects fallaron, rollback ejecutado")
```

---

## 7. API Surface definitiva

### 7.1 Contrato completo

```
POST /api/v3/step
  Input:  { "session_id": "sess-..." }
  Output: StepResult

POST /api/v3/submit  
  Input:  { "session_id": "sess-...", "data": {...}, "idempotency_key": "..." }
  Output: SubmitResult

POST /api/v3/status
  Input:  { "session_id": "sess-..." }
  Output: StatusResult

POST /api/v3/transition
  Input:  { "session_id": "sess-...", "gate": "...", "reason": "..." }
  Output: TransitionResult

POST /api/v3/session
  Input:  { "action": "init|resume|suspend|terminate" }
  Output: SessionResult

GET  /api/v3/health
  Output: { "ok": bool, "version": "3.0", "uptime": 12345 }
```

### 7.2 StepResult (el más importante)

```json
{
  "session_id": "sess-abc123",
  "trace_id": "a1b2c3d4e5f6",
  "current": "ANALYZE.ANALYZE",
  
  "execution": "llm_task",
  "prompt": "Analiza 6→3 capas usando el contexto recopilado...",
  "input_schema": {
    "q1": {"type": "string"},
    "a1": {"type": "string"}
  },
  "required_fields": ["q1", "a1", "q2", "a2", "q3", "a3"],
  
  "context": {
    "session_id": "sess-abc123",
    "task_id": "TASK-002",
    "node": "ANALYZE",
    "sub_state": "ANALYZE"
  },
  
  "state_snapshot": {
    "current_node": "ANALYZE",
    "current_sub_state": "ANALYZE",
    "sub_state_index": 1,
    "history": ["SPRINT.ACTIVE", "ANALYZE.GATHER"],
    "updated_at": "2026-07-23T..."
  },
  
  "meta": {
    "trace_id": "a1b2c3d4e5f6",
    "execution_ms": 42
  }
}
```

### 7.3 SubmitResult

```json
{
  "ok": true,
  "trace_id": "a1b2c3d4e5f6",
  
  "result": {
    "passed": true,
    "findings": [],
    "summary": "Análisis completo: 3/3 preguntas respondidas"
  },
  
  "side_effects": [
    {"type": "update_cpg", "status": "ok"},
    {"type": "save_ckg", "status": "ok"}
  ],
  
  "next": {
    "execution": "user_input",
    "sub_state": "REVIEW",
    "user_message": "Diseño listo. ¿Aprobar / Rechazar / Refinar?",
    "options": [
      {"value": "approve", "label": "Aprobar"},
      {"value": "reject", "label": "Rechazar"},
      {"value": "refine", "label": "Refinar"}
    ]
  },
  
  "state_snapshot": {
    "current_node": "ANALYZE",
    "current_sub_state": "REVIEW",
    "sub_state_index": 2
  }
}
```

### 7.4 Código de la API layer

```python
class WorkflowAPI:
    """API surface del workflow engine v3.
    
    SRP: Unica responsabilidad = traducir llamadas externas a Use Cases.
    SSOT: Toda la lógica está en los Use Cases, no aquí.
    """
    
    def __init__(self, orchestrator: OrchestrationService):
        self._orchestrator = orchestrator
    
    def step(self, session_id: str) -> StepResult:
        """Avanza al siguiente paso del workflow.
        
        Regla 1: Si execution=auto, ejecuta automáticamente.
        Regla 2: Si execution=llm_task|user_input|manual, devuelve instrucciones.
        """
        ctx = self._build_context(session_id)
        return self._orchestrator.step(ctx)
    
    def submit(self, session_id: str, data: dict, idempotency_key: str = "") -> SubmitResult:
        """Procesa datos enviados por el LLM/usuario.
        
        idempotency_key: si se re-envía la misma key, no se reprocesa.
        """
        ctx = self._build_context(session_id)
        ctx.baggage["idempotency_key"] = idempotency_key
        return self._orchestrator.submit(ctx, data)
    
    def status(self, session_id: str) -> StatusResult:
        """Solo lectura: estado actual del workflow."""
        return self._orchestrator.status(session_id)
    
    def transition(self, session_id: str, gate: str, reason: str = "") -> TransitionResult:
        """Transición inter-nodo con validación de precondiciones."""
        ctx = self._build_context(session_id)
        return self._orchestrator.transition(ctx, gate, reason)
    
    def session(self, action: str, **params) -> SessionResult:
        """Gestiona ciclo de vida de sesión."""
        return self._orchestrator.session(action, params)
    
    def _build_context(self, session_id: str) -> ExecutionContext:
        session = self._orchestrator.session_store.load(session_id)
        return ExecutionContext.create(
            session_id=session_id,
            chain_dir=session.chain_dir,
            task_id=session.task_id,
        )
```

---

## 8. Ciclo de vida de sesión

### 8.1 Estados

```
                    ┌─────────────┐
                    │  CREATED     │  ← session(action="init")
                    └──────┬──────┘
                           ↓
                    ┌─────────────┐
              ┌────→│  ACTIVE     │  ← sesión en curso
              │     └──────┬──────┘
              │            ↓
              │     ┌─────────────┐
              │     │ SUSPENDED   │  ← session(action="suspend") — pausa
              │     └──────┬──────┘
              │            ↓
              │     ┌─────────────┐
              └─────│  ACTIVE     │  ← session(action="resume")
                    └──────┬──────┘
                           ↓
                    ┌─────────────┐
                    │ TERMINATED  │  ← session(action="terminate")
                    └─────────────┘
```

### 8.2 Session data

```python
@dataclass
class WorkflowSession:
    """Sesión de workflow.
    
    SRP: Almacena metadata de la sesión, NO el estado del sprint.
    El SprintState se almacena separadamente (SRP).
    """
    session_id: str
    chain_dir: str
    project_id: str = ""
    sprint_id: str = ""
    task_id: str = ""
    status: SessionStatus = SessionStatus.CREATED
    created_at: str = ""
    last_active_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    
    # Checkpoint para recovery
    last_checkpoint: str = ""           # SubStateKey serializado
    snapshot_path: str = ""             # Ruta al último snapshot


class SessionStatus(str, Enum):
    CREATED = "created"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    TERMINATED = "terminated"
```

### 8.3 Recovery automático

```
On restart:
  1. Cargar última sesión activa desde SessionStore
  2. Cargar último snapshot desde SnapshotStore
  3. Reconstruir SprintState
  4. Verificar consistencia (checkpoint vs estado actual)
  5. Si ok → resume en el mismo sub-estado
  6. Si no ok → replay desde event_log
```

---

## 9. Contract Enforcement

### 9.1 Niveles de validación

```
Nivel 0 — Sin contrato (legacy)
  Solo required_fields como strings

Nivel 1 — Schema básico
  input_schema/output_schema como JSON Schema draft-07
  Validación al submit

Nivel 2 — Pre/Post condiciones
  preconditions: ["TASK-002 completada"]
  postconditions: ["sub_state_data.GATHER.fields != null"]

Nivel 3 — Invariantes  
  invariants: [
    "current_node in nodes.keys()",
    "sub_state_index >= 0",
    "!current_node or current_sub_state != ''"
  ]

Nivel 4 — Contratos de edge
  edge.contract: {
    pre: "ANALYZE.REVIEW.decision == approve",
    post: "IMPLEMENT.USER_GATE is pending"
  }
```

### 9.2 ContractValidator

```python
class ContractValidator:
    """Validador de contratos multi-nivel.
    
    Nivel 1: JSON Schema validation
    Nivel 2: Pre/post condiciones como expresiones
    Nivel 3: Invariantes del workflow
    Nivel 4: Contratos de edge
    """

    def validate_input(self, sub_state: SubState, data: dict) -> ValidationReport: ...
    def validate_output(self, sub_state: SubState, data: dict) -> ValidationReport: ...
    def validate_preconditions(self, state: SprintState, graph: WorkflowGraph, gate: str) -> ValidationReport: ...
    def validate_invariants(self, state: SprintState, graph: WorkflowGraph) -> ValidationReport: ...
    def validate_edge_contract(self, gate: Gate, state: SprintState) -> ValidationReport: ...


@dataclass
class ValidationReport:
    ok: bool
    errors: list[ContractViolation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ContractViolation:
    path: str          # input_schema.q1 | preconditions[0] | invariant[2]
    message: str
    level: str         # error | warning
    rule: str          # Schema rule or expression
```

---

## 10. Observabilidad enterprise

### 10.1 Structured Logging

Cada operación del engine produce un log estructurado:

```json
{
  "timestamp": "2026-07-23T15:45:45.608Z",
  "level": "info",
  "session_id": "sess-abc123",
  "trace_id": "a1b2c3d4e5f6",
  "span_id": "f6e5d4c3",
  "operation": "submit",
  "sub_state": "ANALYZE.ANALYZE",
  "execution": "llm_task",
  "duration_ms": 1234,
  "result": "ok",
  "side_effects": ["update_cpg:ok", "save_ckg:ok"]
}
```

### 10.2 Métricas por componente

| Componente | Métrica | Tipo | Target |
|-----------|---------|------|--------|
| API | `api.step.latency_ms` | Histograma | < 100ms p50, < 500ms p99 |
| API | `api.submit.latency_ms` | Histograma | < 200ms p50 |
| Engine | `engine.execution.{type}.count` | Contador | — |
| Engine | `engine.execution.{type}.error_rate` | Ratio | < 1% |
| Engine | `engine.execution.{type}.duration_ms` | Histograma | < 30s auto, < 120s llm |
| Engine | `engine.transition.count` | Contador | — |
| Registry | `registry.task.{name}.count` | Contador | — |
| Registry | `registry.task.{name}.duration_ms` | Histograma | < 5s |
| SideEffects | `side_effect.{type}.count` | Contador | — |
| SideEffects | `side_effect.{type}.error_rate` | Ratio | < 5% |
| Session | `session.{status}.count` | Gauge | — |

### 10.3 Tracing distribuido

```
step() ─→ Orchestrator ─→ ContractValidator ─→ TaskRegistry ─→ SideEffectEngine
  │           │                │                   │                │
  ├─ trace    ├─ span          ├─ span             ├─ span          ├─ span
  │  id=a1    │  orchestrator  │  validate_input   │  execute       │  update_cpg
  │           │                │                   │                │
  │           ├─ span          ├─ span             │                ├─ span
  │           │  step          │  validate_pre     │                │  save_ckg
  │           │                │                   │                │
  └───────────┴────────────────┴───────────────────┴────────────────┘
```

### 10.4 Audit trail

```python
@dataclass
class AuditEntry:
    """Entrada de auditoría inmutable."""
    timestamp: str
    session_id: str
    operation: str                        # step | submit | transition | session
    actor: str                            # llm | user | engine | system
    sub_state: str                        # ANALYZE.ANALYZE
    before: dict                          # Snapshot del estado antes
    after: dict                           # Snapshot del estado después
    data: dict                            # Datos del cambio
    result: str                           # ok | error | timeout
    error: str | None = None
    duration_ms: int = 0
```

---

## 11. Error Taxonomy y Recovery

### 11.1 Jerarquía de errores

```
WorkflowError
├── ValidationError           → Input inválido, campos faltantes
│   ├── SchemaViolation       → No cumple JSON Schema
│   ├── MissingFieldError     → Campo requerido faltante
│   └── InvalidOptionError    → Opción inválida en user_input
├── ExecutionError            → La tarea falló
│   ├── TaskNotFoundError     → TaskRegistry no encuentra la acción
│   ├── TaskTimeoutError      → La tarea excedió timeout_ms
│   ├── TaskFailedError       → La tarea retornó ok=False
│   └── SideEffectError       → Un side-effect falló
├── StateError                → El estado del workflow es inválido
│   ├── InvalidTransitionError → La transición no existe
│   ├── PreconditionError     → Precondiciones no cumplidas
│   ├── InvariantViolation    → Invariante del workflow rota
│   └── NodeNotFoundError     → Nodo no existe en el grafo
├── SessionError              → Problema de sesión
│   ├── SessionNotFoundError  → session_id no existe
│   ├── SessionExpiredError   → Sesión expiró por inactividad
│   └── SessionTerminatedError → Sesión ya terminada
└── InfrastructureError       → Error de infraestructura
    ├── PersistenceError      → No se pudo leer/escribir
    ├── ScriptError           → Script externo falló
    └── TimeoutError          → Timeout de infraestructura
```

### 11.2 Recovery strategies

| Error | Estrategia | Acción |
|-------|-----------|--------|
| SchemaViolation | Retry | Reintentar con validación más estricta |
| TaskFailedError | Retry + escalate | 3 retries, si persiste → on_failure |
| TaskTimeoutError | Retry with longer timeout | 1 retry con timeout 2x, si falla → on_failure |
| SideEffectError | Compensate | Rollback de side-effects ejecutados |
| PreconditionError | Block | Devolver error al LLM, no reintentar |
| SessionExpiredError | Reinit | Session(action="init") automático |
| PersistenceError | Retry + circuit break | 3 retries, si persiste → circuito abierto |
| ScriptError | Log + continue | Si script es opcional, continuar |

### 11.3 Circuit Breaker

```python
@dataclass
class CircuitBreakerState:
    name: str
    state: str = "closed"       # closed | open | half-open
    failure_count: int = 0
    last_failure_at: str = ""
    threshold: int = 5
    recovery_timeout_ms: int = 30000
    half_open_max_requests: int = 1
```

---

## 12. Esquema workflow.json v3

### 12.1 Sub-estado v3 (diff sobre v2)

```json
{
  "key": "ANALYZE",
  "label": "Análisis 6→3 capas",
  "execution": "llm_task",
  "action": "analyze_capas",
  
  "input_schema": {
    "type": "object",
    "properties": {
      "cpg_context": {"type": "object"},
      "ckg_context": {"type": "object"}
    }
  },
  
  "output_schema": {
    "type": "object",
    "properties": {
      "q1": {"type": "string"},
      "a1": {"type": "string"}
    },
    "required": ["q1", "a1"]
  },
  
  "prompt": "Analiza 6→3 capas usando el contexto: {cpg_context}",
  "on_success": "REVIEW",
  "on_failure": "GATHER",
  
  "side_effects": [
    {"type": "update_cpg", "params": {"paths": "artefactos_impactados"}, "optional": false},
    {"type": "save_ckg", "params": {"nodes": ["decision-042"]}, "optional": true}
  ],
  
  "retry": {
    "max_attempts": 3,
    "backoff": "exponential",
    "initial_delay_ms": 2000
  },
  
  "timeout_ms": 180000,
  "on_entry_message": "Ejecutando análisis..."
}
```

### 12.2 Edge v3

```json
{
  "from": "ANALYZE",
  "to": "IMPLEMENT",
  "gate": "analysis_approved",
  "description": "Análisis aprobado, pasar a implementación",
  
  "contract": {
    "pre": "REVIEW.decision == 'approve'",
    "post": "USER_GATE.status == 'pending'"
  },
  
  "transform": {
    "action": "prepare_implementation_context",
    "input": ["ANALYZE.*"],
    "output": "IMPLEMENT.context"
  }
}
```

### 12.3 Schema versioning

```
workflow.json → workflow.v3.json → workflow.v4.json
  
  - Cada versión tiene su schema en schema/workflow.v3.schema.json
  - El loader detecta la versión y aplica el mapper correspondiente
  - Mapper legacy v2→v3:
    - mode: "auto" → execution: "auto"
    - mode: "manual" → execution: "manual"
    - auto_complete → action: "run_script" + side_effects legacy
    - data_contract.fields → required_fields + input_schema básico
    - on_failure_loop → retry + on_failure
    
  - Auto-migration on load: si el schema es v2, el loader lo transforma a v3
```

---

## 13. Roadmap de migración v2 → v3

### Fase 1: Fundación (sprint actual + 1)

| Tarea | Descripción | Depende de |
|-------|-------------|-----------|
| TASK-003 | ✅ DDD: domain/models.py, services.py, errors.py | TASK-002 |
| TASK-008 | **NUEVA:** ExecutionType enum + SubState extendido | TASK-003 |
| TASK-009 | **NUEVA:** TaskResult[T] + ExecutionContext | TASK-003 |

### Fase 2: TaskRegistry y SideEffects

| Tarea | Descripción | Depende de |
|-------|-------------|-----------|
| TASK-010 | **NUEVA:** TaskRegistry (interfaz + InMemory impl) | TASK-009 |
| TASK-011 | **NUEVA:** SideEffectEngine (execute + compensate) | TASK-009 |
| TASK-012 | **NUEVA:** Registro de tareas del catálogo v3 | TASK-010 |

### Fase 3: API Surface

| Tarea | Descripción | Depende de |
|-------|-------------|-----------|
| TASK-013 | **NUEVA:** OrchestrationService (step/submit/transition) | TASK-010 |
| TASK-014 | **NUEVA:** WorkflowAPI (4 endpoints + session) | TASK-013 |
| TASK-015 | **NUEVA:** commands.py refactor → consume API | TASK-014 |

### Fase 4: Enterprise

| Tarea | Descripción | Depende de |
|-------|-------------|-----------|
| TASK-016 | **NUEVA:** ContractValidator multi-nivel | TASK-013 |
| TASK-017 | **NUEVA:** SessionManager + recovery | TASK-014 |
| TASK-018 | **NUEVA:** Observabilidad (logger, metrics, tracing) | TASK-014 |
| TASK-019 | **NUEVA:** Error taxonomy + circuit breaker | TASK-014 |

### Fase 5: workflow.json v3

| Tarea | Descripción | Depende de |
|-------|-------------|-----------|
| TASK-020 | **NUEVA:** Schema v3 + legacy mapper | TASK-015 |
| TASK-021 | **NUEVA:** Migrar workflow.json actual a v3 | TASK-020 |
| TASK-022 | **NUEVA:** Tests e2e flujo completo v3 | TASK-021 |

---

## Apéndice A: Comparativa v2 ↔ v3 Enterprise

| Característica | v2 Actual | v3 Enterprise |
|----------------|-----------|---------------|
| Execution types | 2 (auto/manual) | 5 (bootstrap, auto, llm_task, user_input, manual) |
| Task model | Scripts shell | Tareas puras registrables con tipos |
| Side-effects | Inline en use cases | Engine dedicado con retry + compensation |
| Error handling | ValidationError genérico | Jerarquía completa con recovery strategies |
| Observabilidad | prints + logs ad-hoc | Logger estructurado, métricas, tracing, audit |
| Session | String ID sin lifecycle | Estados formales + recovery automático |
| Contract validation | required_fields strings | JSON Schema + pre/post/invariants |
| API surface | commands.py CLI | step/submit/status/transition/session/health |
| Idempotencia | ❌ No existe | ✅ IdempotencyKey en submit |
| Circuit breaker | ❌ No existe | ✅ Por componente (SideEffect, Persistence) |
| Checkpoint/Restart | ❌ No existe | ✅ Snapshot automático por sub-estado |
| Saga pattern | ❌ No existe | ✅ Compensación automática de side-effects |

---

## Apéndice B: Glosario

| Término | Definición |
|---------|-----------|
| **ExecutionType** | Quién ejecuta la acción de un sub-estado |
| **TaskRegistry** | Registro de tareas puras accionables por nombre |
| **TaskResult[T]** | Resultado tipado de una tarea pura |
| **SideEffect** | Efecto secundario declarativo sin lógica de dominio |
| **ExecutionContext** | Contexto de ejecución transportado entre capas |
| **Contract** | Pre/Post condiciones + schema que validan el flujo |
| **Saga** | Secuencia de operaciones con compensación en caso de fallo |
| **Circuit Breaker** | Patrón de resiliencia que evita llamadas a componentes fallidos |
| **TraceLog** | Registro inmutable append-only de evidencia de ejecución del workflow |

---

## Apéndice C: TraceLog — Evidencia Inmutable de Ejecución

> Añadido: 2026-07-23. Requisito transversal: verificar que el LLM ejecuta realmente lo que declara.

### C.1 Problema

El LLM recorre el workflow y ejecuta tareas en cada nodo, pero **no hay evidencia verificable** de que realmente hace lo que dice:

- El LLM dice "revisé el código" → pero ¿ejecutó realmente el script de review?
- El LLM dice "analicé el contexto" → pero ¿entró al sub-estado GATHER?
- Hay actividades silenciosas sin traza

El agente **no puede manipular** las trazas porque el engine las escribe.

### C.2 Solución: TraceLog

Archivo **append-only** `trace.log` en el chain_dir del sprint. Cada línea es un JSON con la evidencia de una operación:

```jsonl
{"ts":"2026-07-23T15:00:00.000Z","type":"NODE_ENTRY","node":"SPRINT","trace_id":"a1b2","actor":"engine"}
{"ts":"2026-07-23T15:00:01.000Z","type":"SUB_STATE","node":"SPRINT","state":"ACTIVE","trace_id":"a1b2","actor":"engine"}
{"ts":"2026-07-23T15:00:02.000Z","type":"AUTO_EXECUTE","script":"python3 spring.py status","exit_code":0,"stdout":"Tasks: 3 pending","stderr":"","duration_ms":450,"trace_id":"a1b2","actor":"engine"}
{"ts":"2026-07-23T15:00:05.000Z","type":"SUBMIT","node":"ANALYZE","state":"ANALYZE","fields":{"q1":"analisis...","a1":"respuesta..."},"trace_id":"a1b2","actor":"llm"}
{"ts":"2026-07-23T15:00:06.000Z","type":"TRANSITION","from":"ANALYZE","to":"IMPLEMENT","gate":"analysis_approved","preconditions_ok":true,"trace_id":"a1b2","actor":"engine"}
{"ts":"2026-07-23T15:00:10.000Z","type":"ERROR","error_type":"ValidationError","message":"campo requerido faltante","trace_id":"a1b2","actor":"engine"}
```

### C.3 Tipos de Trace

| TraceType | Quién lo escribe | Evidencia que captura | Falsificable por LLM? |
|-----------|-----------------|----------------------|----------------------|
| `NODE_ENTRY` | Engine (step/transition) | Nodo al que se entró | ❌ No |
| `SUB_STATE` | Engine (step/advance) | Sub-estado actual + índice | ❌ No |
| `AUTO_EXECUTE` | Engine (ScriptRunner) | Script, stdout, stderr, exit_code, duration_ms | ❌ No |
| `SUBMIT` | Engine (submit use case) | Sub-estado + campos enviados (sin valores sensibles) | ❌ No |
| `TRANSITION` | Engine (transition) | Gate, from→to, preconditions result | ❌ No |
| `SESSION` | Engine (session manager) | init/resume/suspend/terminate | ❌ No |
| `ERROR` | Engine (error handler) | Tipo, mensaje, stack opcional | ❌ No |

**Regla de oro**: el `actor` siempre lo asigna el engine. El LLM no puede escribir al TraceLog directamente.

### C.4 Integración en la arquitectura

```
┌──────────────────────────────────────────────────────────────────┐
│                         TraceLog                                  │
├──────────────────────────────────────────────────────────────────┤
│  TraceLogger (ESCRIBE)                  TraceReporter (LEE)       │
│  - append(entry) → trace.log           - report(chain) → Markdown│
│  - solo engine puede llamarlo           - state_diff() → Δ tree   │
│  - auto-flush en cada write             - evidence_gaps() → []    │
├──────────────────────────────────────────────────────────────────┤
│                    Puntos de inyección                             │
│  ScriptRunner.run()     → AUTO_EXECUTE (con stdout/stderr/exit)   │
│  OrchestrationService   → NODE_ENTRY + SUB_STATE + TRANSITION    │
│  SubmitDataUseCase      → SUBMIT                                 │
│  SessionManager         → SESSION                                │
│  Error handler          → ERROR (antes de relanzar)               │
└──────────────────────────────────────────────────────────────────┘
```

### C.5 Post-Sprint: TraceReporter

```bash
python3 spring.py trace <chain_id>
```

Output:

```
📊 TRACE REPORT — workflow-engine-v2-20260723
═══════════════════════════════════════════
Sesión: sess-abc123 | Duración: 45m | Trazas: 142

ÁRBOL DE EJECUCIÓN:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SPRINT.ACTIVE          ✅ engine (on_entry)
  └─ transition: start_task → ANALYZE       ✅ engine

ANALYZE.GATHER         ✅ engine (on_entry)
  └─ AUTO_EXECUTE: cpg.py update            ✅ exit:0 (3 files)
  └─ AUTO_EXECUTE: task.py research         ✅ exit:0 (context OK)

ANALYZE.ANALYZE        ✅ LLM entró
  └─ SUBMIT: {q1, a1, q2, a2, q3, a3}     ✅ 6/6 campos

ANALYZE.REVIEW         ✅ LLM entró
  └─ SUBMIT: {decision: approve}            ✅ con evidence

IMPLEMENT.USER_GATE    ✅ LLM entró
  └─ SUBMIT: {decision: proceed}            ✅

IMPLEMENT.WORK         ✅ auto
  └─ AUTO_EXECUTE: task.py advance          ✅ exit:0 (5 archivos)

IMPLEMENT.CODE_REVIEW  ✅ auto
  └─ AUTO_EXECUTE: code_review_scan.py      ✅ exit:0 (0 findings)

VERIFY.QA_RUN          ✅ auto
  └─ AUTO_EXECUTE: pytest -x -q             ✅ exit:0 (15/15 tests)

VERIFY.PO_ACCEPT       ✅ LLM entró
  └─ SUBMIT: {decision: accept}             ✅ con feedback

EVIDENCIA:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ 12/12 sub-estados con trace
✅ 6/6 AUTO_EXECUTE con stdout capturado
✅ 4/4 SUBMIT con campos registrados
❌ 0 gaps de evidencia

COMPARACIÓN ÁRBOL ESTADOS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Estados visitados coinciden con workflow.json
✅ Transiciones siguen edges válidos
✅ Sin ciclos inválidos ni saltos

VEREDICTO: 🎯 Workflow respetado al 100%
```

### C.6 Garantías

1. **Inmutabilidad**: append-only. Nunca se modifica una entrada existente.
2. **No-repudio**: el actor lo asigna el engine. El LLM no puede escribir trazas falsas.
3. **Evidencia técnica**: `AUTO_EXECUTE` captura stdout/stderr/exit_code reales del script.
4. **Verificabilidad post-mortem**: después del sprint, el reporte muestra exactamente qué pasó.
5. **Traza distribuida**: `trace_id` correlaciona todas las entradas de una misma operación.

### C.7 Diferencia con DomainEvents

| Aspecto | DomainEvent (events.py) | TraceLog (nuevo) |
|---------|------------------------|-----------------|
| Propósito | Event Sourcing / Replay | Auditoría / Verificación |
| Formato | En memoria + JSON | Append-only JSONL |
| Retención | Hasta próxima compactación | Todo el ciclo de vida del sprint |
| Quién escribe | Engine (use cases) | Engine (ScriptRunner + Orchestrator) |
| Incluye evidencia | ❌ metadata del evento | ✅ stdout/stderr/exit_code |
| Post-procesamiento | replay() para reconstruir estado | report() para audit trail |
| Falsificable por LLM | Parcialmente (si escribe al log) | ❌ No (engine-only write) |

---

*Documento generado: 2026-07-23. Diseño enterprise basado en workflow_v3_design.md y análisis de gaps del motor v2 actual.*
