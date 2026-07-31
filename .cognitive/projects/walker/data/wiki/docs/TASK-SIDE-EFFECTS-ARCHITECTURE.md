# Task & Side-Effect Architecture

> **Estado:** Implementado  
> **Versión:** 1.0  
> **ADR:** decision-024  
> **Tareas registradas:** 21  
> **Side-effects registrados:** 8  

Tasks and side-effects are organized as **self-registering modules**, not a monolithic catalog.

## Directory Structure

```
scripts/
├── tasks/                    ← One file per domain
│   ├── planning.py           ← plan_sprint, show_dashboard, validate_plan
│   ├── gathering.py          ← gather_context, gather_task_context
│   ├── implementation.py     ← implement_task, review_code, run_tests
│   ├── verification.py       ← verify_predicates
│   ├── refinement.py         ← refine_diagnose, route_to_target, escalate_refine
│   ├── knowledge.py          ← session_init, save_to_ckg, close_sprint, collect_knowledge, analyze_capas, review_analysis
│   └── transforms.py         ← prepare_analysis_context, build_task_briefing...
│
├── side_effects/             ← One file per side-effect type
│   ├── cpg_updates.py        ← update_cpg, build_project_graph, update_code_index, update_delta_graph
│   ├── ckg_persist.py        ← save_ckg, save_to_ckg, delete_ckg_node, rollback_ckg
│   ├── events.py             ← emit_event
│   └── archive.py            ← archive_sprint, restore_cpg_snapshot
│
└── _walker/workflow_engine/
    ├── domain/
    │   └── registry.py       ← TaskRegistry interface (register/execute/list/get)
    ├── infrastructure/
    │   ├── task_registry.py  ← InMemoryTaskRegistry
    │   └── side_effects.py   ← SideEffectEngine (execute + compensate)
    └── bootstrap.py          ← Discovers tasks/ and side_effects/, calls register()
```

## How Registration Works

Each module exports a `register(registry)` function. The engine calls it at startup:

```python
# tasks/planning.py
from _walker.workflow_engine.domain.models import ExecutionContext, TaskResult

def plan_sprint(ctx: ExecutionContext, data: dict) -> TaskResult:
    ...

def register(registry):
    registry.register("plan_sprint", plan_sprint)
    registry.register("show_dashboard", show_dashboard)
```

```python
# bootstrap.py — called once at engine startup (auto-discovery)
# No need to import each module manually — bootstrap discovers them:

from _walker.workflow_engine.bootstrap import bootstrap

registry, se_engine = bootstrap()
# Descubre automáticamente tasks/*.py y side_effects/*.py
# Cada módulo con función register() se auto-registra
```

## Rules

- Each **task handler** is a **pure function**: `(ExecutionContext, dict) -> TaskResult`
- Each **side-effect handler** returns `dict` (not TaskResult): `(ExecutionContext, dict) -> dict`
- No handler contains workflow logic (no advancing sub-states, no transitions)
- Adding a new task = new file in `tasks/` — no existing code modified
- Adding a new side-effect = new file in `side_effects/`
- The workflow grows organically without touching central registries
- `bootstrap()` auto-descubre todos los módulos con `register()` — no requiere imports manuales

## Complete Task Map (workflow.v3)

| Action | File | Type |
|--------|------|------|
| `plan_sprint` | `tasks/planning.py` | task |
| `show_dashboard` | `tasks/planning.py` | task |
| `validate_plan` | `tasks/planning.py` | task |
| `gather_context` | `tasks/gathering.py` | task |
| `gather_task_context` | `tasks/gathering.py` | task |
| `implement_task` | `tasks/implementation.py` | task |
| `review_code` | `tasks/implementation.py` | task |
| `run_tests` | `tasks/implementation.py` | task |
| `verify_predicates` | `tasks/verification.py` | task |
| `refine_diagnose` | `tasks/refinement.py` | task |
| `route_to_target` | `tasks/refinement.py` | task |
| `escalate_refine` | `tasks/refinement.py` | task |
| `session_init` | `tasks/knowledge.py` | task |
| `save_to_ckg` | `tasks/knowledge.py` | task |
| `close_sprint` | `tasks/knowledge.py` | task |
| `collect_knowledge` | `tasks/knowledge.py` | task |
| `analyze_capas` | `tasks/knowledge.py` | task |
| `review_analysis` | `tasks/knowledge.py` | task |
| `prepare_analysis_context` | `tasks/transforms.py` | transform |
| `build_task_briefing` | `tasks/transforms.py` | transform |
| `build_verification_context` | `tasks/transforms.py` | transform |
| `update_cpg` | `side_effects/cpg_updates.py` | side-effect |
| `build_project_graph` | `side_effects/cpg_updates.py` | side-effect |
| `update_code_index` | `side_effects/cpg_updates.py` | side-effect |
| `save_ckg` | `side_effects/ckg_persist.py` | side-effect |
| `delete_ckg_node` | `side_effects/ckg_persist.py` | side-effect (compensation) |
| `rollback_ckg` | `side_effects/ckg_persist.py` | side-effect (compensation) |
| `emit_event` | `side_effects/events.py` | side-effect |
| `archive_sprint` | `side_effects/archive.py` | side-effect |
| `restore_cpg_snapshot` | `side_effects/archive.py` | side-effect (compensation) |
