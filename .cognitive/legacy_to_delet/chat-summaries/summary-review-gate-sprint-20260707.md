## Goal
Implementar un Review Gate completo en el MCP donde el MCP guía al LLM con instrucciones de loop prompting, recolecta evidencia CFG, bloquea hasta confirmación del usuario, y solo avanza el StateGraph cuando el usuario aprueba. Corregir bugs en dispatch_pipeline (_sid no asignado), project_board (task_count=0), y sprint node IDs no descriptivos.

## Constraints & Preferences
- **Solo el humano puede decir que algo está terminado** — el MCP no debe auto-avanzar tareas
- review_mode configurable: "task" (review por tarea) o "sprint" (review al completar todas)
- Host tools (todowrite, Edit, bash) bypassan dispatch_pipeline — el MCP no puede controlarlas
- El hook post-op existe (GPS injection) pero estaba incompleto para cubrir toda la máquina de estados
- El `_sid` bug impedía usar health, code_lookup, decisions vía MCP
- tasks creadas via project(create_task) quedaban invisibles en task_count

## Progress
### Done
- **Review Gate v2**: review_loop.py con review_after_commit(), confirm_task_advance(), confirm_sprint_advance(), review_after_task(), review_after_sprint(), _build_loop_prompting_instruction()
- **review_mode flag**: delta_create_sprint(review_mode="task"|"sprint") con metadata en StateGraph, project_board() lo expone
- **confirm_task()**: dispara review_after_task() si mode=task, o review_after_sprint() si mode=sprint y última tarea
- **confirm_sprint()**: approved=True → op_repo_commit() + op_cog_commit() + _reset_session_file_tracker() + _clear_blocked(). approved=False → solo _clear_blocked() + feedback
- **DoD persistence**: _init_dod() se llama desde create_sprint() y create_task()
- **_sid bug fix**: dispatch_pipeline línea 749 (session_id=_hints_sid desde params) y línea 1215 (params.get en vez de p.get)
- **task_count fix**: project_board() suma child nodes al task_count, legacy path filtrado por parent_id+children
- **Sprint node IDs**: new_node_id(goal) genera STATE-{slug}-{hex}
- **GPS_STATUS_REVIEW_PENDING**: nuevo estado en _determine_state() que detecta BLOCKED y inyecta loop prompting
- **Auto-discovery proyecto**: Status 2 (no project) ahora escanea repos vía _discover_repos()
- **DoD alerts en GPS**: _check_dod_alerts() inyecta advertencias en la instrucción de navegación
- **Decisiones alert en post-op**: cuando mcp-write detecta decisions_persisted=false, inyecta alerta
- **Sprint sin cerrar**: GPS_STATUS_COMPLETED chequea git diff, sugiere confirm_sprint si hay cambios sin commit
- **7 gap analysis tasks** creadas en el sprint STATE-e91cc986e43b

### In Progress
- (ninguno — sprint completado)

### Blocked
- **_sid fixes requieren reinicio del MCP server** para tomar efecto (los fixes están en código pero no cargados)
- **Host tools bypass**: todowrite, Edit, bash no pasan por dispatch_pipeline — no hay solución desde el MCP, requiere cambios en opencode

## Key Decisions
- **Sistema de revisión con dos modos (task/sprint)**: el usuario necesita poder elegir entre revisar cada tarea o todo el sprint al final
- **confirm_task() y confirm_sprint() son los únicos puntos de avance del StateGraph**: ninguna otra herramienta puede marcar tareas como completadas
- **GPS injection en _engine_dispatch() es el hook post-op**: corre después de cada MCP tool y antes de que el LLM reciba la respuesta; debe inyectar el estado completo del MCP
- **new_node_id(goal) incluye slug**: STATE-{slug}-{hex} en vez de solo hash, para legibilidad en logs y project_board

## Next Steps
- Reiniciar MCP server para que fixes del _sid tomen efecto
- Verificar que health, code_lookup, decisions funcionan post-restart
- Considerar implementar pre-op gate que intercepte host tools (requiere cambios en opencode)
- Cerrar el sprint con confirm_sprint(approved=True)

## Critical Context
- **Branch**: master (trabajo directo sobre main)
- **Commits**: 771974c (review-pending + fixes), 0fc1141 (DoD alerts + decisions alert + sprint-close)
- **Sprint activo**: STATE-e91cc986e43b con goal "Implementar sistema de revision con dos modos task/sprint con loop prompting + validacion CFG + confirmacion de usuario"
- **MCP no reiniciado**: los cambios de código están commiteados pero el proceso del MCP server sigue corriendo con el código anterior
- **`_sid` bug**: "cannot access local variable '_sid' where it is not associated with a value" en dispatch_pipeline línea 749 — afecta health, code_lookup, decisions, y otros tools consolidados

## Relevant Files
- [cognitive_v2/shared/review_loop.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/shared/review_loop.py): review_after_commit(), confirm_task_advance(), confirm_sprint_advance(), review_after_task(), review_after_sprint(), _build_loop_prompting_instruction()
- [cognitive_v2/shared/dispatch_pipeline.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/shared/dispatch_pipeline.py): _engine_dispatch() — bug _sid líneas 749 y 1215, post-op hook con GPS injection + DoD auto-mark + decisions alert
- [cognitive_v2/features/project/application/project_service.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/project/application/project_service.py): create_sprint(review_mode), create_task(_init_dod), get_project_board(task_count corregido)
- [cognitive_v2/features/project/domain/state_graph.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/project/domain/state_graph.py): new_node_id(goal) con slug
- [cognitive_v2/features/guidance/domain/cognitive_state.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/guidance/domain/cognitive_state.py): _determine_state() con GPS_STATUS_REVIEW_PENDING, auto-discovery, DoD alerts, sprint-close check
- [cognitive_v2/features/project/api/mcp_tools.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/project/api/mcp_tools.py): confirm_task(), confirm_sprint(), delta_create_sprint(review_mode)
- [cognitive_v2/shared/helpers/main.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/shared/helpers/main.py): _BLOCKED_EXEMPT_OPS con confirm_task, _set_blocked/_clear_blocked/_is_blocked, _check_write_enforcement, _discover_repos
- [cognitive_v2/shared/dod_contract.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/shared/dod_contract.py): _init_dod(), _get_dod(), _mark_dod_step(), _get_dod_status()
- [docs/SPRINT_REVIEW_GATE.md](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/docs/SPRINT_REVIEW_GATE.md): plan del sprint con DoD de cada tarea
