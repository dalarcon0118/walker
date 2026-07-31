"""decision_capture.py — Side-effect: captura decisiones del PO en el CKG.

MECANISMO: registro AUTOMATICO de decisiones en el CKG al completar un
nodo de decision del workflow (approve/revise/refine/accept/reject).

No depende de que el agente recuerde llamar kg_store: el workflow lo
hace por el. Cada decision queda como nodo CKG tipo 'decision' con:
  - decision: el valor elegido (approve, refine, ...)
  - rationale: la razon del PO (feedback/evidence)
  - node/sub_state: donde se tomo
  - task_id, session_id, timestamp

Esto implementa la persistencia de decisiones como mecanismo
estructural, no como estrategia de agente.
"""
from __future__ import annotations
from typing import Any
from libs.shared_domain import ExecutionContext


def capture_decision(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return {"ok": False, "error": "chain_dir not available"}

    decision = params.get("decision", "") or ""
    rationale = params.get("feedback", "") or params.get("evidence", "") or ""
    sub_state = params.get("sub_state", "") or "?"

    # Si el workflow no paso la decision en params, leerla del estado:
    # el submit la guardo en sub_state_data del sub-estado actual.
    if not decision:
        try:
            from features.engine.infrastructure.repositories import WorkflowStateRepository
            state = WorkflowStateRepository().load(chain_dir)
            ss_data = state.get_sub_state_data(state.current_sub_state)
            decision = ss_data.fields.get("decision", "") or ""
            rationale = (rationale
                         or ss_data.fields.get("feedback", "")
                         or ss_data.fields.get("evidence", "")
                         or "")
            sub_state = f"{state.current_node}.{state.current_sub_state}"
        except Exception:
            pass

    if not decision:
        return {"ok": True, "captured": False, "reason": "sin decision que capturar"}

    try:
        from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph
        from features.project import ProjectService

        paths = ProjectService.resolve_paths(chain_dir)
        ckg = CodeKnowledgeGraph(str(paths.project_root))
        result = ckg.save_nodes([{
            "type": "decision",
            "label": f"decision:{decision}",
            "statement": (
                f"Decision '{decision}' tomada en {sub_state}"
                + (f": {rationale}" if rationale else "")
            ),
            "metadata": {
                "decision": decision,
                "rationale": rationale,
                "sub_state": sub_state,
                "task_id": ctx.task_id or "",
                "source": f"session:{ctx.session_id}",
            },
        }])
        return {"ok": True, "captured": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e), "captured": False}


def register(engine: Any) -> None:
    engine.register("capture_decision", capture_decision)
