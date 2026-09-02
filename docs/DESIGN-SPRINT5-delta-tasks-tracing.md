# Design Sprint 5 — Delta Tracking + Tasks + Event Tracing

**Status:** DRAFT — pending approval before coding
**Date:** 2026-09-01
**ADR reference:** ADR-WALKER-071 §4 Fase 3 (Sprint 5 deferred)

---

## 1. Objective

Port the remaining ~14k LOC from legacy to `fast_mcp/`:
- `delta_tracking/` (6,428 LOC) — sprint/spec management, ISprintService adapter
- `event_tracing/` (1,324 LOC) — trace events, ITracer adapter
- `shared/tasks/` (6,643 LOC) — task registry, action handlers, side effects

**Exit criteria:**
- All 31 legacy tools registered and functional in fast_mcp
- ISprintService, ITracer, SideEffectEngine have real adapters (not stubs)
- Zero imports from `mcp/` (already verified, must remain true)
- 165+ existing tests still green
- `mcp/` can be deleted (Phase 4)

---

## 2. Rules (non-negotiable)

| # | Rule | Rationale |
|---|------|-----------|
| R-5.1 | **Domain-first**: port domain entities/values first, then application, then infrastructure | Consistent with Sprints 1-4 |
| R-5.2 | **Simplify during port**: collapse use cases into service methods, remove ceremony | Prevents LOC bloat |
| R-5.3 | **One handler pattern**: flat handlers in `handlers/` (same as Phase 2 + Phase 3) | Consistency |
| R-5.4 | **No service locator**: all wiring via composition.py | ADR-WALKER-071 §3bis |
| R-5.5 | **Result pattern**: all operations return `Result[T, E]` or dict | Convention-001 |
| R-5.6 | **Fail-open**: tools that fail return `{error: str(e)}`, never raise | MCP protocol |
| R-5.7 | **fast_mcp NEVER imports mcp/**: already verified, must remain true | Autonomy |

---

## 3. What gets ported vs simplified

### delta_tracking (6,428 LOC → ~2,000 LOC estimated)

| Original | Ported as | Simplification |
|----------|-----------|----------------|
| `domain/entities.py` (259 LOC) | `domain/entities.py` (~150 LOC) | Simplify Sprint/Spec entities |
| `domain/values.py` (63 LOC) | `domain/values.py` (~40 LOC) | Status enums, IDs |
| `domain/events.py` (176 LOC) | `domain/events.py` (~80 LOC) | Remove unused events |
| `domain/services/` (3 files, 674 LOC) | `domain/services/` (2 files, ~200 LOC) | Merge conflict_resolver into spec_service |
| `application/services/sprint_service.py` (392 LOC) | `application/services/sprint_service.py` (~150 LOC) | Collapse use cases into methods |
| `application/services/delta_query_service.py` (71 LOC) | `application/services/delta_query_service.py` (~50 LOC) | As-is |
| `application/services/session_capture_service.py` (129 LOC) | **Deleted** | Session capture not needed for MCP |
| `application/use_cases/` (17 files, ~1,400 LOC) | **Collapsed into sprint_service** | Use cases are thin wrappers |
| `application/ceremonies.py` (1,448 LOC) | **Deleted** | Ceremony logic is CLI-specific |
| `application/projections.py` (83 LOC) | **Deleted** | Projections are CLI-specific |
| `application/task_synthesis.py` (270 LOC) | **Deleted** | Task synthesis is CLI-specific |
| `application/index_service.py` (178 LOC) | **Deleted** | Index service is CLI-specific |
| `application/memory_orchestrator.py` (211 LOC) | **Deleted** | Memory orchestrator is CLI-specific |
| `application/evolutionary_document_model.py` (144 LOC) | **Deleted** | Document model is CLI-specific |
| `infrastructure/cognitive_delta_repo.py` (180 LOC) | `infrastructure/cognitive_delta_repo.py` (~100 LOC) | Simplify |
| `infrastructure/adapters/walker_delta_adapter.py` (171 LOC) | `infrastructure/adapters/walker_delta_adapter.py` (~80 LOC) | Simplify |

### event_tracing (1,324 LOC → ~400 LOC estimated)

| Original | Ported as | Simplification |
|----------|-----------|----------------|
| `domain/entities.py` (107 LOC) | `domain/entities.py` (~60 LOC) | TraceEvent simplified |
| `domain/events.py` (56 LOC) | `domain/events.py` (~30 LOC) | As-is |
| `domain/values.py` (66 LOC) | `domain/values.py` (~40 LOC) | As-is |
| `application/reflection.py` (243 LOC) | **Deleted** | Reflection is CLI-specific |
| `application/search_service.py` (19 LOC) | `application/search_service.py` (~15 LOC) | As-is |
| `infrastructure/repository.py` (264 LOC) | `infrastructure/repository.py` (~150 LOC) | Simplify |

### shared/tasks (6,643 LOC → ~1,500 LOC estimated)

| Original | Ported as | Simplification |
|----------|-----------|----------------|
| `actions/` (28 files, 3,715 LOC) | `actions/` (~800 LOC) | Only action handlers needed by MCP tools |
| `actions/workflow_tools.py` (171 LOC) | **Deleted** | Already ported in Phase 3 |
| `actions/sprint_planning.py` (196 LOC) | `actions/sprint_planning.py` (~80 LOC) | Simplify |
| `actions/implement.py` (114 LOC) | `actions/implement.py` (~60 LOC) | Simplify |
| `actions/verify.py` (193 LOC) | `actions/verify.py` (~80 LOC) | Simplify |
| `actions/capture.py` (196 LOC) | `actions/capture.py` (~80 LOC) | Simplify |
| `actions/trace_push.py` (102 LOC) | `actions/trace_push.py` (~60 LOC) | Simplify |
| `actions/intelligence.py` (198 LOC) | `actions/intelligence.py` (~80 LOC) | Simplify |
| `actions/` (20 other files, ~2,545 LOC) | **Deleted or inline** | Most are thin wrappers |
| `side_effects/` (14 files, 556 LOC) | `side_effects/` (~200 LOC) | Only needed side effects |
| `domain/` (2 files, 117 LOC) | `domain/` (~60 LOC) | TaskInput VO |
| `infrastructure/` (5 files) | **Deleted** | Infrastructure is CLI-specific |
| `bootstrap.py` (140 LOC) | **Deleted** | Replaced by composition.py |

---

## 4. Directory Structure

```
fast_mcp/features/
├── delta_tracking/                    # NEW: sprint/spec management
│   ├── __init__.py
│   ├── domain/
│   │   ├── __init__.py
│   │   ├── entities.py               # Sprint, Spec, DeltaState
│   │   ├── values.py                 # Status enums, IDs
│   │   └── events.py                 # DeltaEvent
│   ├── application/
│   │   ├── __init__.py
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── sprint_service.py     # SprintApplicationService (facade)
│   │   │   └── delta_query_service.py # Query delta state
│   │   └── ports/
│   │       └── delta_repository.py   # IDeltaRepository ABC
│   ├── infrastructure/
│   │   ├── __init__.py
│   │   └── cognitive_delta_repo.py   # CognitiveDeltaRepository
│   └── handlers/
│       ├── __init__.py               # Registry + dispatch
│       └── sprint.py                 # handle_sprint_planning, handle_resolve_project_sprint
│
├── event_tracing/                    # NEW: trace events
│   ├── __init__.py
│   ├── domain/
│   │   ├── __init__.py
│   │   ├── entities.py               # TraceEvent
│   │   ├── values.py                 # TraceQuery
│   │   └── events.py                 # TraceEventRecorded
│   ├── application/
│   │   ├── __init__.py
│   │   └── services/
│   │       └── trace_service.py      # TraceService (record, search)
│   ├── infrastructure/
│   │   ├── __init__.py
│   │   └── repository.py             # CognitiveTraceRepository
│   └── handlers/
│       ├── __init__.py               # Registry + dispatch
│       └── trace.py                  # handle_trace_push
│
└── tasks/                            # NEW: task registry + action handlers
    ├── __init__.py
    ├── domain/
    │   ├── __init__.py
    │   └── validated_vo.py           # TaskInput
    ├── application/
    │   ├── __init__.py
    │   └── task_registry.py          # TaskRegistry (register + dispatch)
    ├── actions/
    │   ├── __init__.py
    │   ├── implement.py              # implement_task, run_tests
    │   ├── verify.py                 # verify_predicates
    │   ├── capture.py                # capture
    │   ├── trace_push.py             # trace_push
    │   ├── intelligence.py           # intelligence
    │   ├── sprint_planning.py        # sprint_planning, resolve_project_sprint
    │   ├── code_review.py            # review_code
    │   ├── refine.py                 # refine_*
    │   ├── research.py               # research_topic
    │   ├── context_gathering.py      # context_gathering
    │   ├── project_update.py         # update_project
    │   └── knowledge_*.py            # knowledge tasks (if not already in Phase 2)
    ├── side_effects/
    │   ├── __init__.py
    │   └── registry.py               # SideEffectRegistry (register + dispatch)
    └── handlers/
        ├── __init__.py               # Registry + dispatch
        └── tasks.py                  # Generic task handler (dispatches to actions)
```

---

## 5. Integration with existing code

### 5.1 ISprintService adapter

```python
# fast_mcp/features/delta_tracking/application/ports/delta_repository.py

class ISprintService(ABC):
    """Port for workflow engine to query sprint state."""
    
    @abstractmethod
    def load_tasks(self, chain_dir: str) -> list[dict]:
        """Load tasks for the current sprint."""
        
    @abstractmethod
    def get_sprint(self, chain_dir: str) -> dict | None:
        """Get current sprint info."""
```

The adapter implements this port using CognitiveDeltaRepository.

### 5.2 ITracer adapter (replaces NoopTracer)

```python
# fast_mcp/features/event_tracing/infrastructure/tracer_adapter.py

class CognitiveTracer(ITracer):
    """Real tracer using CognitiveGraph trace index."""
    
    def __init__(self, trace_service: TraceService):
        self._service = trace_service
    
    def trace(self, event: WorkflowEvent) -> None:
        self._service.record(TraceEvent.from_workflow_event(event))
```

### 5.3 SideEffectEngine (replaces NoOpSideEffectEngine)

```python
# fast_mcp/features/tasks/side_effects/registry.py

class SideEffectRegistry:
    """Registry of side effect handlers."""
    
    def __init__(self):
        self._handlers: dict[str, Callable] = {}
    
    def register(self, event_type: str, handler: Callable):
        self._handlers[event_type] = handler
    
    def run(self, event_type: str, data: dict) -> Result[dict, str]:
        handler = self._handlers.get(event_type)
        if not handler:
            return Success({"skipped": True, "reason": "no handler"})
        return handler(data)
```

### 5.4 TaskRegistry

```python
# fast_mcp/features/tasks/application/task_registry.py

class TaskRegistry:
    """Registry of task handlers (replaces legacy TaskRegistry)."""
    
    def __init__(self):
        self._tasks: dict[str, TaskDefinition] = {}
    
    def register(self, name: str, handler: Callable, input_schema: dict, ...):
        self._tasks[name] = TaskDefinition(...)
    
    def execute(self, name: str, ctx: ExecutionContext, data: dict) -> TaskResult:
        task = self._tasks.get(name)
        if not task:
            return TaskResult.failure(f"Unknown task: {name}")
        return task.handler(ctx, data)
```

### 5.5 Composition wiring

```python
# fast_mcp/composition.py — additions

def build_delta_context(chain_dir: str) -> DeltaContext:
    """Wire delta_tracking for a project."""
    # ... build delta_repository, sprint_service, etc.

def build_trace_context(chain_dir: str) -> TraceContext:
    """Wire event_tracing for a project."""
    # ... build trace_repository, trace_service, tracer

def build_tasks_context(chain_dir: str) -> TasksContext:
    """Wire task registry for a project."""
    # ... build task_registry, side_effect_registry
```

---

## 6. Migration Order

### Sprint 5.1: delta_tracking domain + application

1. Port `domain/entities.py` (Sprint, Spec, DeltaState)
2. Port `domain/values.py` (Status enums)
3. Port `domain/events.py` (DeltaEvent)
4. Port `domain/services/spec_service.py` (simplified)
5. Port `application/services/sprint_service.py` (collapsed use cases)
6. Port `application/services/delta_query_service.py`
7. Port `application/ports/delta_repository.py`
8. **Test:** domain + application unit tests

### Sprint 5.2: delta_tracking infrastructure + handlers

1. Port `infrastructure/cognitive_delta_repo.py`
2. Create `handlers/sprint.py` (sprint_planning, resolve_project_sprint)
3. Wire DeltaContext in composition.py
4. Register tools in phase3_tasks.py
5. **Test:** integration tests

### Sprint 5.3: event_tracing

1. Port `domain/entities.py` (TraceEvent)
2. Port `domain/values.py` (TraceQuery)
3. Port `application/services/trace_service.py`
4. Port `infrastructure/repository.py`
5. Create `CognitiveTracer` (replaces NoopTracer)
6. Create `handlers/trace.py` (trace_push)
7. Wire TraceContext in composition.py
8. **Test:** integration tests

### Sprint 5.4: shared/tasks domain + task registry

1. Port `domain/validated_vo.py` (TaskInput)
2. Create `application/task_registry.py` (TaskRegistry)
3. Port action handlers (implement, verify, capture, trace_push, intelligence, sprint_planning, code_review, refine, research, context_gathering, project_update)
4. Create `side_effects/registry.py` (SideEffectRegistry)
5. Wire TasksContext in composition.py
6. **Test:** integration tests

### Sprint 5.5: handlers + tool registration

1. Create `handlers/tasks.py` (generic task dispatcher)
2. Register all remaining tools in phase3_tasks.py
3. Update composition.py with all contexts
4. Run full test suite
5. **Test:** E2E tests for all 31 tools

---

## 7. Numbers

| Feature | Original LOC | Ported LOC | Reduction |
|---------|-------------|------------|-----------|
| delta_tracking | 6,428 | ~2,000 | -69% |
| event_tracing | 1,324 | ~400 | -70% |
| shared/tasks | 6,643 | ~1,500 | -77% |
| **Total** | **14,395** | **~3,900** | **-73%** |

| Metric | Before Sprint 5 | After Sprint 5 |
|--------|-----------------|----------------|
| Total tools | 21 | **31** |
| Total tests | 165 | **200+** |
| `fast_mcp/` LOC | ~8,000 | **~12,000** |
| Legacy `mcp/` LOC | ~65,000 | **0** (deletable) |

---

## 8. failure_scenarios

```json
[
  {
    "fallo": "ISprintService adapter no puede cargar tasks porque delta_tracking no tiene datos",
    "causa_raiz": "proyecto sin sprint activo o .cognitive/ corrupto",
    "mitigacion": "adapter retorna lista vacia; sprint_planning tools manejan caso vacio",
    "deteccion": "test: load_tasks en proyecto sin sprint retorna []"
  },
  {
    "fallo": "CognitiveTracer falla al guardar evento porque trace index no existe",
    "causa_raiz": "trace index no inicializado en .cognitive/",
    "mitigacion": "CognitiveTracer crea el index si no existe (lazy init)",
    "deteccion": "test: trace en proyecto nuevo crea trace index"
  },
  {
    "fallo": "TaskRegistry no encuentra task porque el handler no esta registrado",
    "causa_raiz": "falta registrar una task en composition.py",
    "mitigacion": "TaskRegistry.execute retorna TaskResult.failure con nombre de task",
    "deteccion": "test: execute task inexistente retorna failure"
  },
  {
    "fallo": "SideEffectRegistry ejecuta handler que lanza excepcion",
    "causa_raiz": "handler tiene bug o dependencia no disponible",
    "mitigacion": "SideEffectRegistry envuelve handler en try/except; retorna Success con skipped=True",
    "deteccion": "test: handler que lanza retorna success con skipped"
  },
  {
    "fallo": "CognitiveDeltaRepository no puede resolver graph_factory",
    "causa_raiz": "CognitiveGraph no disponible en .cognitive/",
    "mitigacion": "lazy init con fallback a None; error claro en runtime",
    "deteccion": "test: repository sin graph retorna error descriptivo"
  },
  {
    "fallo": "Circular import entre delta_tracking/ y workflow/ al resolver puertos",
    "causa_raiz": "delta_tracking importa workflow/domain/ports.py",
    "mitigacion": "ISprintService es puerto en workflow/domain/ports.py; delta_tracking lo implementa",
    "deteccion": "test de autonomia: delta_tracking/ no importa workflow/ directamente"
  },
  {
    "fallo": "Tool count excede 31 despues de portar shared/tasks",
    "causa_raiz": "duplicacion de tools entre Phase 2 y shared/tasks",
    "mitigacion": "verificar que tools de knowledge ya estan en Phase 2; no duplicar",
    "deteccion": "test: server arranca con exactamente 31 tools"
  },
  {
    "fallo": "Action handler falla porque depende de AppContainer (service locator)",
    "causa_raiz": "handler del legacy usa resolve_locator() en vez de inyeccion",
    "mitigacion": "handlers nuevos reciben dependencias via composition.py, no via locator",
    "deteccion": "test: handler no llama resolve_locator()"
  },
  {
    "fallo": "CognitiveTraceRepository usa formato de evento incompatible con legacy",
    "causa_raiz": "campos renombrados o eliminados en el port",
    "mitigacion": "TraceEvent mantiene compatibilidad de campos con legacy; roundtrip test",
    "deteccion": "test: TraceEvent.to_dict() produce formato compatible"
  },
  {
    "fallo": "Sprint 5 tests fallan porque mocking no cubre todos los puertos",
    "causa_raiz": "nuevos puertos (IDeltaRepository, ITraceRepository) no mockeados",
    "mitigacion": "tests usan MockDeltaRepository, MockTraceRepository (in-memory)",
    "deteccion": "test suite completa en verde"
  }
]
```

---

## 9. Success Criteria

- [ ] 31 tools registered in fast_mcp (3 Phase 1 + 14 Phase 2 + 4 Phase 3 + 10 Sprint 5)
- [ ] ISprintService has real CognitiveDeltaRepository adapter
- [ ] ITracer has real CognitiveTracer adapter (replaces NoopTracer)
- [ ] SideEffectEngine has real SideEffectRegistry (replaces NoOpSideEffectEngine)
- [ ] TaskRegistry registered with all action handlers
- [ ] delta_tracking domain: Sprint, Spec, DeltaState entities ported
- [ ] event_tracing domain: TraceEvent entity ported
- [ ] All 31 tools callable via MCP
- [ ] Zero imports from mcp/ (already verified)
- [ ] 165+ existing tests still green
- [ ] New tests: 35+ (domain, integration, E2E)
- [ ] Total fast_mcp/ LOC: ~12,000 (not 14k as-is)
- [ ] mcp/ deletable (Phase 4 can proceed)
