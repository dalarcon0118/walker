1. Unificar el canal: exponer workflow_state/status como tools MCP para que el agente no dependa del CLI del plugin.
2. Sacar _di global: inyectarlo en el ExecutionContext o en el bundle; tests más simples y razonamiento local.
3. Partir knowledge.py: ya lo pide la deuda (cyclo 151); separar brief, preflight, what_changed, plan_diff.
4. Versionar data/workflow: o eliminar la dependencia del JSON en runtime y tenerlo todo en código (como ya se hace con artifacts.py).
5. Puente skill ↔ walker: el gate required_skill_gate solo cuenta load_skill vía walker CLI; el skill de opencode no emite el trace. Unificarlos.
6. Auto-advance más inteligente: el motor avanza pasos auto pero todavía exige muchos submit manuales; los gates user_input podrían auto-presentar y esperar sin que el agente orqueste.
7. Arreglar el scoping del CPG en review_code (que respete files y reporte files_scanned real) — lo tocamos, pero el adaptador sigue escaneando el proyecto entero para métricas.
8. Formalizar los fixes transversales (gate-report, transform_spec robust, CLI decoupling) como ADR/spec, para que no queden como "correcciones huérfanas".
• Dry-run (submit --dry-run): requiere ramificar la persistencia; más invasivo. Recomendado como spec propia.
• Tool de discovery con schemas ricos y parámetros documentados (_human, _payload, view): mejora incremental del registro MCP.
• Trace consultable con schema de filtros documentado (hoy search_memories rechaza filtros no soportados sin decir cuáles son válidos).
• Skill gate unificado (problema 5): alto esfuerzo, spec propia.
## Medidos 2026-11-26 (post-planificación, backend-microservices/SPEC-001)

9. **`review_code` certificaba con 0 archivos (falso verde).** El problema 7 era el scoping; el
   caso medido fue peor: `_files` resolvía la spec con `current_spec(task_id=...)` **sin acotar
   por sprint**, y con `SPEC-001` presente a la vez en un sprint pausado y en el activo el
   helper devuelve `None` (guard de ambigüedad). Resultado: `files=[]` → `passed = not findings`
   → **`passed: true`**, resumen «0 archivos: sintaxis ok; CPG no disponible». Fix:
   `_files` resuelve sprint (sesión → activo) con `spring_id`/`exact`, igual que
   `verify_predicates`; y `review_code` devuelve `passed: False` + `blocked_reason` cuando no
   hay archivos (misma regla que SPEC-014: 0 evidencia no es verde). Tests:
   `test_sin_archivos_no_certifica_la_revision`.

10. **`kg_query` sin `task_id`: el gate `required_tools` era insatisfacible por MCP.** Las tools
    de memoria (`recall_knowledge`, `search_memories`, `code_search`) emiten `kg_query` vía
    `_emit_kg_query` sin `task_id` (no conocen la tarea en curso), pero `has_kg_query` filtraba
    `ev.task_id == task_id` → ningún evento calificaba y `submit` se bloqueaba aunque el agente
    hubiera consultado CKG y CPG (medido: hubo que empujar el evento a mano con `trace_push` y
    el submit pasó recién desde el proceso CLI). Además `has_kg_query` ignoraba `layers`: una
    consulta al CKG satisfacía un requisito de CPG. Fix: `has_kg_query` acepta eventos sin
    `task_id` (nunca los atribuidos a otra tarea) y exige intersección con las capas pedidas.
    Tests: 3 nuevos en `tests/test_fast_mcp_event_tracing_bdd.py` (sin task_id, capa ajena,
    otra tarea). Gate del repo: `python3 -m pytest -q` → 1876 passed.

## Medido 2026-11-26 (canal de decisión)

11. **El canal humano está atado al CLI, no a la procedencia.** `_human` lo setea solo el CLI
    (`cli.py:151-153`, «El CLI es el canal humano»). Por MCP, una decisión que el usuario YA
    eligió en la UI se rechaza con `agent_decision_blocked`, así que el agente termina pidiéndole
    al usuario que pegue un comando. Eso invierte el costo: la decisión existió en el chat y el
    motor la ignora porque no vino del proceso «correcto». Fix propuesto: la procedencia viaja en
    el submit (`decided_by: user|agent`, `channel: chat|cli`, referencia al turno/opción elegida)
    y la setea el cliente cuando el usuario responde; el gate valida procedencia, no proceso.
    Límite honesto: sin un token firmado por el cliente, sigue siendo auditoría + convención — hoy
    cualquier agente que haga shell-out al CLI obtiene `_human=True` sin autorización.

12. **`capture_knowledge` no recibía la sesión en `data`.** En KNOWLEDGE_UPDATE el efecto
    secundario corre con el `ctx` de la sesión, pero el handler resolvía la identidad solo con
    `data["session_id"]`/`spring_id`; sin ellos, un `SPEC-NNN` (que se repite por sprint) es
    ambiguo y el cierre falla con «pasá spring_id o session_id». Al fallar cayó a WORK y volvió a
    re-correr el review (medido 2026-11-26, SPEC-002). Fix: `_seed_session` completa desde
    `ctx.session_id` (ignora `mcp`) y `_capture_scope_error` acepta el sprint ACTIVO como
    desambiguador. Tests: 3 nuevos en `tests/test_capture_side_effects_bdd.py`.
