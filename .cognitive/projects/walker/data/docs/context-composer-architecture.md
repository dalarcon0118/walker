# ContextComposer Architecture

## Overview

The ContextComposer replaces flat prompt templates with a **declarative context system**. Each workflow node **declares** which context layers it needs, and the Composer assembles them at runtime from registered providers.

```
.node.json → context.sources → registry.get(provider) → collect() → ContextPiece[] → ComposedContext
                              → context.focus    → system message
.node.json → prompt_id       → .prompt.json     → template with {placeholders}
```

## Architecture Layers

```
┌─────────────────────────────────────────────────────────┐
│                   OrchestrationService                    │
│  step() → _build_node_briefing() → composer.compose()     │
├─────────────────────────────────────────────────────────┤
│                   ContextComposer                         │
│  compose(node_config, prompt_template, ctx)               │
│  ├─ 1. collect_pieces(sources, ctx)                       │
│  ├─ 2. window.allocate(pieces)    ← token budget          │
│  ├─ 3. build system_message from focus + instructions      │
│  ├─ 4. resolve {placeholders} from pieces + ctx            │
│  └─ 5. return ComposedContext { system, layers, user }     │
├─────────────────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│  │ TaskProv │ │StateProv │ │CKGProv  │ │CPGProv   │    │
│  │(plan.json│ │(sprint   │ │(knowledge│ │(code graph│    │
│  │ No query)│ │ state)   │ │ graph)   │ │ graph)    │    │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘    │
│  ┌──────────┐ ┌──────────┐                               │
│  │TraceProv │ │DeltaProv │                               │
│  │(events)  │ │(task hist)│                               │
│  └──────────┘ └──────────┘                               │
├─────────────────────────────────────────────────────────┤
│                   QueryManager                            │
│  query_layer(name, filters) → CKG/CPG/Trace/State/Delta  │
└─────────────────────────────────────────────────────────┘
```

## The 6 Context Layers

| Layer | Provider | Source | Purpose | When to use |
|-------|----------|--------|---------|-------------|
| **task** | `TaskProvider` | `plan.json` (reads directly) | Full 22-field task specification | Sub-estados que **consumen** una tarea |
| **state** | `StateProvider` | Sprint state via QueryManager | Current sprint context, history, scope | **Todos** los sub-estados |
| **ckg** | `CKGProvider` | Cognitive Knowledge Graph | Patterns, decisions, conventions | Diseño, análisis, planning |
| **cpg** | `CPGProvider` | Code Property Graph | Structured code entities (functions, classes, calls) | Análisis de código, implementación |
| **trace** | `TraceProvider` | TraceGraph | Session events, errors, transitions | Diagnóstico de errores, refinement |
| **delta** | `DeltaProvider` | DeltaGraph | Goal gap, historical tasks | Sprint planning, retro |

### Layer details

#### task (priority: 5)
- **Source**: `plan.json` en `chain_dir` (NO usa QueryManager)
- **Config**: ninguno (inyecta todos los campos disponibles)
- **Fields**: 22 del schema: `task_id`, `objective`, `result_state`, `task_description`, `role`, `scrum_role`, `type`, `work_type`, `domain`, `importer_files`, `depends_on`, `preconditions`, `acceptance_criteria`, `quality_gates`, `validation`, `loop_verification`, `loop_acceptance`, `artefactos_impactados`, `risks`, `decisions`, `refinement`, `task_context`

#### state (priority: 0)
- **Source**: Sprint state via QueryManager → `_query_state()`
- **Fields**: `node`, `task_id`, `sprint_id`, `sprint_goal`, `sprint_status`, `history`
- **Query**: `{"state": {}}` (todo), o por `node`, `task`, `status`

#### ckg (priority: 10)
- **Source**: CKG via QueryManager
- **Types**: decision (52), convention (72), pattern (5), component (6), module (10), etc.
- **Query filters**: `type`, `scope`, `label`, `task`
- **Default config**: `{"query": {"type": "decision"}, "max_nodes": 15}`

#### cpg (priority: 10)
- **Source**: CPG via QueryManager
- **Types**: function, class, file, module, constant
- **Relations**: CALLS, CONTAINS, IMPORTS
- **Query filters**: `file`, `function`, `type`, `calls`, `bug`
- **Default config**: `{"query": {"file": "_walker"}, "max_results": 20}`

#### trace (priority: 50)
- **Source**: TraceGraph via QueryManager
- **Events**: chronological session events
- **Default config**: `{"max_events": 5-10}`

#### delta (priority: 50)
- **Source**: DeltaGraph via QueryManager
- **Content**: task history, goal gaps
- **Default config**: `{"max_results": 10}`

## Node Briefing

Inyectado por `OrchestrationService._build_node_briefing()` en todos los `step()` calls para LLM_TASK, USER_INPUT, MANUAL. No es un provider — es metadata del flujo.

```
[Node Briefing]
Node: ANALYZE.ANALYZE
Phase: analyze
Task: TASK-001
Sprint: sprint-2026-07
Chain: /chains/test-flow-ast
```

## Sub-estado → Context Configuration

| Node | Sub-state | Layers | Providers |
|------|-----------|--------|-----------|
| `analyze` | ANALYZE | 5 | task + state + ckg + cpg + trace |
| `analyze` | KNOWLEDGE_CAPTURE | 2 | task + state |
| `design` | DESIGN_ANALYZE | 4 | task + state + ckg + cpg |
| `refine` | DIAGNOSE | 3 | task + cpg + trace |
| `refine` | KNOWLEDGE_CAPTURE | 2 | task + state |
| `implement` | KNOWLEDGE_UPDATE | 2 | task + state |
| `project_init` | GATHER_SPECS | 1 | state |
| `sprint` | PLANNING | 3 | state + ckg + delta |
| `sprint` | RETRO | 3 | state + ckg + delta |
| `sprint_planning` | GOAL | 3 | state + ckg + delta |
| `sprint_planning` | ADD_TASKS | 2 | state + delta |
| `sprint_planning` | REVIEW | 2 | state + ckg |

### When to use each layer combination

```
Consumes a task?    → include task (plan.json)
Needs code context? → include cpg (structured code)
Needs patterns?     → include ckg (decisions/conventions)
Needs history?      → include trace (session events)
Needs planning?     → include delta (goal gaps)
Always include      → state (sprint context)
```

## Provider Interface

```python
class ContextProvider(ABC):
    def instructions(self, config: dict, ctx: Any) -> str:
        """Declaración de capa para el LLM — qué contiene, cómo usarla."""
        return ""

    def collect(self, config: dict, ctx: Any, query_manager: Any = None) -> ContextPiece | None:
        """Recupera datos concretos de esta capa."""
        ...

    def schema(self) -> dict:
        """JSON Schema de la config que acepta este provider."""
        return {}
```

### Adding a new provider

```python
from .registry import ContextProvider
from .models import ContextPiece

class MyProvider(ContextProvider):
    def instructions(self, config, ctx):
        return "I am layer X — it contains..."

    def collect(self, config, ctx, query_manager=None):
        data = query_manager.query_layer("my_layer", config.get("query", {}))
        return ContextPiece(
            provider="my_layer",
            name="my context",
            instructions=self.instructions(config, ctx),
            content=_format_my_data(data),
            tokens=len(content)//4,
            priority=10,
        )

# Register
registry.register("my_layer", MyProvider())
```

## Token Budget

### Cost per layer (estimated)

| Layer | Data tokens | Instructions | Total |
|-------|-------------|-------------|-------|
| task | ~320 | ~600 | ~920 |
| state | ~20 | ~250 | ~270 |
| ckg (15 nodes) | ~450 | ~350 | ~800 |
| cpg (20 matches) | ~400 | ~400 | ~800 |
| trace (10 events) | ~500 | ~250 | ~750 |
| delta (10 results) | ~375 | ~250 | ~625 |

### Total per sub-state

| Sub-state | Tokens | % of 8k limit |
|-----------|--------|----------------|
| GATHER_SPECS | ~730 | 9% |
| KNOWLEDGE_CAPTURE / UPDATE | ~1,050 | 13% |
| ADD_TASKS | ~1,360 | 17% |
| REVIEW (sprint_planning) | ~1,530 | 19% |
| GOAL / PLANNING / RETRO | ~2,160 | 27% |
| DIAGNOSE | ~2,330 | 29% |
| DESIGN_ANALYZE | ~2,650 | 33% |
| ANALYZE | ~3,400 | 43% |

All sub-states are within practical limits (6-8k tokens). Node Briefing adds ~62 tokens. Prompt templates average ~200 tokens.

### Configuration

Token budget can be configured per node in `context.budget`:

```json
"budget": {
  "max_tokens": 6000,
  "reserve_for_response": 2000,
  "strategy": "truncate"
}
```

Strategies: `overflow_warning`, `summarize_fallback`, `truncate`, `strict`.

## File Layout

```
.claude/skills/delta/scripts/_walker/workflow_engine/context/
├── __init__.py          # Exports + create_default_registry()
├── composer.py          # ContextComposer orchestrator
├── models.py            # ContextPiece, ComposedContext, ContextBudget
├── registry.py          # ContextProvider base class + registry
├── providers.py         # 6 concrete providers (Task, State, CKG, CPG, Trace, Delta)
├── query_manager.py     # QueryManager for layer queries
└── window.py            # ContextWindowManager for token budget

.cognitive/projects/delta-skill/workflow.v3/
├── nodes/*.node.json    # Workflow node definitions with context.sources
├── prompts/**/*.prompt.json  # Prompt templates
└── data/
    ├── ckg.json.gz      # Knowledge graph (181 nodes)
    └── cpg.json.gz      # Code property graph (685 nodes)
```

## Migration Guide

To migrate a new sub-state from `on_entry_message` to ContextComposer:

1. **Create a prompt file** at `prompts/<node>/<name>.prompt.json`
2. **Add `context.sources`** to the sub-state in the node.json:
   ```json
   "context": {
     "sources": [
       {"provider": "task", "config": {}},
       {"provider": "state", "config": {"query": {}}}
     ],
     "focus": {
       "role": "description of the LLM's role",
       "constraints": ["instruction 1", "instruction 2"],
       "context_priority": "task (what) → state (scope)",
       "output_instructions": "submit --field 'value'"
     }
   }
   ```
3. **Replace `on_entry_message`** with `"prompt_id": "<node>/<name>"`
4. **Update the prompt template** to reference the layers as "layer above"
5. **Remove** old context tool references (`code_context:*`, `knowledge:search`, etc.)

## Cognitive Optimization (Key Insight for Agents)

Optimizing context for LLMs is NOT primarily about reducing token count.
It is about reducing the **cognitive work** the transformer must perform.

### What costs cognitive work

| Cost Factor | Why it matters | Example |
|-------------|----------------|---------|
| **Multiple voices** | Each provider's `instructions()` is a separate narrative. The LLM must reconcile N voices into one coherent plan. Attention complexity is O(n²). | 6 providers = 6 different "I am the..." introductions |
| **Redundancy** | Same advice in provider.instructions AND focus.constraints. The LLM processes the same guidance twice. | "Read TASK layer FIRST" appears in both places |
| **Self-evident descriptions** | "objective: high-level goal" describes what `objective: Crear funcion...` already makes obvious. Wastes attention. | All 22 field descriptions in TaskProvider |
| **Query syntax in instructions** | `HOW TO QUERY ME` sections teach query syntax the LLM can't execute at inference time. Relevant for devs, noise for the model. | CKG/CPG/Trace/State providers all have these |
| **Attention distance** | Related data in distant layers requires cross-attention to connect. | task.domain=engine + ckg.scope=engine in different blocks |
| **6 independent narratives** | Each provider self-introduces. The LLM must build a relationship graph between them. | "I am the CPG... I am the CKG... I am the Task..."

### Design principles for low-cognitive-load context

1. **One voice**: Provider instructions should read as a SINGLE coherent document, not 6 independent manifestos. Drop "I am the...", use section headers like `=== TASK ===`.

2. **No query syntax in instructions**: `HOW TO QUERY ME` is developer documentation. Remove it from `instructions()` — the LLM doesn't execute queries at inference time.

3. **Place guidance in focus.constraints, not provider.instructions**: `focus.constraints` is processed closer to the final prompt. Move cross-layer guidance there. Keep only WHAT the layer contains in instructions.

4. **No self-evident descriptions**: If the field name and data make it obvious, don't describe it. `objective: Crear funcion...` needs no explanation.

5. **Group by purpose**: For sub-estados with task + cpg + trace, consider a "Spec vs Reality" combined format so the LLM sees the comparison directly instead of building it across attention heads.

6. **Prefer brevity**: Every unnecessary word increases the average attention distance between meaningful tokens. Dense, directive text beats verbose, explanatory text.

### Measured cognitive load per provider (current)

| Provider | Chars | Tokens | Cognitive cost |
|----------|-------|--------|----------------|
| TaskProvider | 2133 | 533 | 22 field descriptions + self-intro + HOW TO USE |
| CPGProvider | 1483 | 370 | Self-intro + query syntax + relations + analysis |
| CKGProvider | 1389 | 347 | Self-intro + node types + edge types + query syntax |
| TraceProvider | 1075 | 268 | Self-intro + event types + query syntax |
| StateProvider | 806 | 201 | Self-intro + query syntax + cross-ref advice |
| DeltaProvider | 671 | 167 | Self-intro + query syntax |
| **Total (6 providers)** | **7557** | **1889** | **6 voices to reconcile** |
| **ANALYZE (5 providers)** | **~6900** | **~1725** | **5 voices + focus.constraints overlap** |

Target for optimization: keep field listings (valuable), remove self-introductions and query syntax, deduplicate with focus.constraints. Estimated reduction: 40-50% cognitive load.
