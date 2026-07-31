## Since Last Summary
- `ops_meta_esquema.py` (863L) movido de `legacy/.cognitive/` a `cognitive_v2/shared/` con enfoque clean code (no absorbido en BC porque `ops_meta.py` tiene 11 funciones dispares que no pertenecen al BC meta)
- `ops_session.py` (3091L) absorbido en `features/cqrs/` con 7 MCP tools (`session_init`, `session_close`, `harden_proposal`, `refine_finalize`, `verify_phase`, `task_intake`, `task_decompose`). shared/ se queda como puente por cross-deps (`_evaluate_invariants` usado por `features/meta/`, `_build_provenance_section` usado por `ops_facts`).
- `ops_facts.py` (4269L) absorbido en `features/decisions/` con 10 MCP tools clean code (< 15 líneas c/u). shared/ se queda como puente por cross-deps con `features/cqrs/bus_setup.py` y `features/auto_facts/`.

## Goal
Hacer que `cognitive_v2/` sea un package Python 100% autocontenido que no dependa de `legacy/.cognitive/`, y absorber los ops_* en bounded contexts de a uno.

## Constraints & Preferences
- Zero dependencia runtime de `legacy/.cognitive/` — debe poder eliminarse.
- El CLI solo prepara el workspace; el MCP server resuelve sus propias dependencias.
- Las ~50 importaciones lazy (`from helpers import _kv`) en features/ no se tocan — compat layer via sys.path.
- Absorción incremental: un ops_*.py por vez en su bounded context correspondiente.
- **Clean code**: cada MCP tool < 20 líneas, guard clauses, SRP, sin comentarios obvios.
- **Ops grandes con cross-deps** (ops_session 3091L, ops_facts 4269L) se mantienen como puente en shared/ — su eliminación requiere absorber las funciones compartidas primero.

## Progress
### Done
- (anteriores) Movidos 24 archivos runtime de `legacy/.cognitive/` a `cognitive_v2/shared/`.
- (anteriores) `cognitive_v2/__init__.py` añade `shared/` a sys.path.
- (anteriores) Eliminados `sys.path.insert` hacks, corregido helpers.py, tests, dispatcher.
- (anteriores) `ops_project.py` y `ops_message.py` absorbidos y eliminados de shared/.
- ✅ **`ops_meta_esquema.py` movido** de legacy a shared/ — desbloquea eliminación de `legacy/.cognitive/`.
- ✅ **`ops_session.py` → `features/cqrs/`**: 7 MCP tools (`register_session_tools` en server.py). shared/ops_session.py queda como puente.
- ✅ **`ops_facts.py` → `features/decisions/`**: 10 MCP tools clean code (`register_facts_tools` en server.py). shared/ops_facts.py queda como puente.
- ✅ Decisions BC ahora suma 3 herramientas pre-existentes (fact, observe, acknowledge_events) + 10 nuevas (snapshot_list, snapshot_show, health_check, export_facts, import_facts, effectiveness_report, diagnose_session, audit_facts, commit_summary, diff_facts).

### In Progress
- N/A (sub-sesiones completadas)

### Blocked
- `shared/ops_session.py` (3091L) — no se puede eliminar hasta que `_evaluate_invariants` (usado por `features/meta/`) y `_build_provenance_section` (usado por `ops_facts`) sean absorbidos.
- `shared/ops_facts.py` (4269L) — no se puede eliminar hasta que `features/cqrs/bus_setup.py` y `features/auto_facts/` dejen de importarlo.

## Key Decisions
1. (anterior) **Sys.path approach vs compat stubs**: sys.path desde `__init__.py` para imports lazy.
2. (anterior) **WORKSPACE_ROOT desde env var**: en vez de `SCRIPT_DIR.parent`.
3. (anterior) **Backward-compat redirects** en dispatcher para ops absorbidos.
4. (anterior) **Uno por uno**: absorción incremental.
5. **Ops_meta_esquema no absorbed en BC**: `ops_meta.py` contiene 11 funciones dispares (onboarding, resume, repo-commit, etc.) que NO son DDD meta-esquema. Solo se movió `ops_meta_esquema.py` que es el archivo que realmente pertenece al BC meta.
6. **Bridge pattern para ops grandes**: `shared/ops_session.py` y `shared/ops_facts.py` se mantienen como puentes porque sus cross-deps (funciones compartidas importadas por otros módulos) no pueden absorberse al mismo tiempo.
7. **Clean code en MCP tools**: Cada tool < 20 líneas con guard clauses, sin lógica duplicada, nombres verbo+objeto.

## Next Steps
1. Absorber `ops_context.py` → `features/context/` (ya existe cell_handler)
2. Absorber 8 ops restantes: cell, plan, skill, stale, metrics, trace, theory_test, age_decay
3. Cuando todos absorbidos: eliminar deps.py, dispatcher.py, legacy/.cognitive/
4. Terminar esta tarea

## Critical Context
- **Branch**: `cognitive/gaps-analysis`
- **HEAD**: `c6dffb9` cog: session summary + `e4ca659` feat: self-contained
- **Tests**: 12/12 pasan (8 fast + 4 slow MCP)
- **shared/ bridges activos**: `ops_session.py`, `ops_facts.py`, `ops_meta.py` — no eliminables aún
- **Nuevos archivos sin commit**: `session_tools.py`, `facts_tools.py`, `ops_meta_esquema.py`

## Relevant Files
- `projects/cognitive-mcp/cognitive_v2/features/cqrs/api/session_tools.py` — 7 session MCP tools (NUEVO)
- `projects/cognitive-mcp/cognitive_v2/features/decisions/api/facts_tools.py` — 10 facts MCP tools clean code (NUEVO)
- `projects/cognitive-mcp/cognitive_v2/shared/ops_meta_esquema.py` — movido de legacy (NUEVO)
- `projects/cognitive-mcp/cognitive_v2/server.py` — register_session_tools + register_facts_tools agregados
- `projects/cognitive-mcp/cognitive_v2/features/decisions/api/mcp_tools.py` — fact, observe, acknowledge_events
- `projects/cognitive-mcp/cognitive_v2/features/cqrs/api/mcp_tools.py` — cqrs_query, cqrs_command
