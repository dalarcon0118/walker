## Since Last Summary
- deps.py eliminado completamente (era ~1600L → 0)
- ops_facts (~4300L) absorbido en `features/decisions/application/facts_service.py`
- ops_session (~3090L) absorbido en `features/session/application/session_service.py`
- Circular-dep roto: `_build_provenance_section` movido de ops_session a facts_service
- 22 feature API files actualizados con imports directos a sus BCs
- 4 nuevos métodos en CodeGraphAdapter (dead_code, complexity, call_chain, class_hierarchy)
- 6 nuevos MCP tools (code_dead_code, code_complexity, code_call_chain, code_class_hierarchy, code_validate_cpg, code_derive_hypotheses)
- cpg_validation.py creado con validate_cpg(), _detect_cpg_regressions(), derive_cgc_hypotheses()
- 10/10 CPG-CGC gap predicates satisfechos
- delta-cpg-cgc-gaps.json actualizado

## Goal
Cerrar todos los gaps de alta prioridad CPG-CGC y completar la migración de deps.py/dispatcher.py a bounded contexts propios.

## Constraints & Preferences
- Backward compat vía stubs (shared/ops_facts.py, shared/ops_session.py) con DeprecationWarning
- Todos los tests deben pasar (1181 pass)
- Solo se persiste en `.cognitive/chat-summaries/`

## Progress
### Done
- ✅ deps.py eliminado completamente (era ~1600L → 0)
- ✅ Session helpers → shared/session.py
- ✅ Dispatch pipeline → shared/dispatch_pipeline.py
- ✅ File watcher → features/monitoring/file_watcher_service.py
- ✅ Project history → features/navigation/application/project_history_service.py
- ✅ Project state/tasks → features/project/application/project_state_service.py
- ✅ Navigation state → features/navigation/application/navigation_service.py
- ✅ WorkingMemory singleton → shared/working_memory.py
- ✅ ops_facts (~4300L) absorbido en features/decisions/application/facts_service.py
- ✅ ops_session (~3090L) absorbido en features/session/application/session_service.py
- ✅ Circular-dep roto (_build_provenance_section movido)
- ✅ 22 feature API imports actualizados (session, helpers, dispatch_pipeline, BCs)
- ✅ CodeGraphAdapter.get_dead_code()
- ✅ CodeGraphAdapter.get_complexity()
- ✅ CodeGraphAdapter.get_call_chain()
- ✅ CodeGraphAdapter.get_class_hierarchy()
- ✅ features/cpg/application/cpg_validation.py (validate_cpg, _detect_cpg_regressions, derive_cgc_hypotheses)
- ✅ MCP tools: code_dead_code, code_complexity, code_call_chain, code_class_hierarchy, code_validate_cpg, code_derive_hypotheses
- ✅ 10/10 CPG-CGC gap predicates satisfechos
- ✅ delta-cpg-cgc-gaps.json actualizado a 10/10
- ✅ 1181 tests pasan (12 pre-existing dual_write failures)

### In Progress
- (ninguno)

### Blocked
- dispatcher.py (~1360L) pendiente de migrar a dispatch_pipeline + BCs

## Key Decisions
1. **Stub pattern para backward compat**: shared/ops_facts.py y shared/ops_session.py se reemplazan con stubs que hacen `from cognitive_v2.features.* import *` con DeprecationWarning. Esto permite actualizar importers incrementalmente sin romper nada.
2. **_build_provenance_section movido a facts_service**: la función construye la sección de procedencia para snapshots (concern de facts/decisions, no de session). Esto rompe el circular-dep ops_session ↔ ops_facts.
3. **Adapter singleton via _get_adapter()**: tanto cpg_validation.py como mcp_tools.py comparten el mismo singleton de CodeGraphAdapter, evitando conexiones duplicadas a CGC.
4. **Delta JSON como fuente de verdad**: se actualiza el archivo delta-cpg-cgc-gaps.json para reflejar 10/10 predicates, documentando el progreso en formato máquina-legible.

## Next Steps
1. Eliminar dispatcher.py (migrar a dispatch_pipeline + BCs)
2. Agregar engine.db e index.json a .gitignore
3. Terminar esta tarea.

## Critical Context
- **Branch**: cognitive/gaps-analysis
- **Parent session**: eliminar-deps-plan (status changed to completed)
- **5 commits en esta sesión**:
  - `56ac63c` — eliminar deps.py + absorber ops_facts/session en BCs (Fases A-H)
  - `f94f362` — add 4 CPG-CGC gap methods + 4 MCP tools
  - `7df67a6` — cpg_validation.py + delta JSON 10/10
  - `5a09144` — delta skill refinements + cognitive state updates
- **Último commit**: `5a09144`
- **Tests**: 1181 pass, 12 pre-existing failures (dual_write)
- **WIP**: dispatcher.py pendiente

## Relevant Files
- `projects/cognitive-mcp/cognitive_v2/deps.py` — **ELIMINADO** (era ~1600L)
- `projects/cognitive-mcp/cognitive_v2/features/decisions/application/facts_service.py` — ops_facts absorbido (4332L)
- `projects/cognitive-mcp/cognitive_v2/features/session/application/session_service.py` — ops_session absorbido (3090L)
- `projects/cognitive-mcp/cognitive_v2/features/cpg/infrastructure/codegraph_adapter.py` — 4 nuevos métodos
- `projects/cognitive-mcp/cognitive_v2/features/cpg/application/cpg_validation.py` — NUEVO (3 funciones)
- `projects/cognitive-mcp/cognitive_v2/features/cpg/api/mcp_tools.py` — 6 nuevos tools
- `projects/cognitive-mcp/cognitive_v2/shared/dispatch_pipeline.py` — dispatch reemplazado
- `delta-cpg-cgc-gaps.json` — 10/10 predicates
- `projects/cognitive-mcp/cognitive_v2/shared/ops_facts.py` — stub (DeprecationWarning)
- `projects/cognitive-mcp/cognitive_v2/shared/ops_session.py` — stub (DeprecationWarning)
