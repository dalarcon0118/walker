"""decision_capture.py — Side-effect: captura decisiones del PO en el CKG.

MECANISMO: registro AUTOMATICO de decisiones en el CKG al completar un
nodo de decision del workflow (approve/revise/refine/accept/reject).

No depende de que el agente recuerde llamar kg_store: el workflow lo
hace por el. Cada decision queda como nodo CKG tipo 'decision' con:
  - decision: el valor elegido (approve, refine, ...)
  - rationale: la razon del PO (feedback/evidence)
  - node/sub_state: donde se tomo
  - task_id, session_id, timestamp

CICLO DE VIDA: si sobre el mismo sub_state ya existia una decision ACTIVA
con valor distinto, esta nueva la REEMPLAZA. Se detecta automaticamente
(sin intervencion del agente) y se pasa via 'supersedes' a save_nodes,
que degrada la anterior (status=superseded + superseded_by + superseded_at).
Asi el CKG siempre refleja la decision VIGENTE sobre cada punto, con historia.

Esto implementa la persistencia de decisiones como mecanismo
estructural, no como estrategia de agente.
"""
from __future__ import annotations
from typing import Any
from libs.shared_domain import ExecutionContext
from features.engine.domain.side_effect_contract import as_side_effect, SCOPE_DECISION


def _detect_superseded(ckg, decision: str, sub_state: str) -> list[str]:
    """Detecta decisiones ACTIVAS previas sobre el mismo sub_state que esta reemplaza.

    Regla: una decision nueva sobre el mismo punto de decision (sub_state)
    con VALOR DISTINTO al previo indica que el PO cambio de opinion → la
    nueva reemplaza a la anterior. Mismo valor = reconfirmacion (no supersede).
    Solo se consideran decisiones ACTIVAS (las ya superseded pertenecen a la
    historia y no vuelven a reemplazarse).
    """
    supersedes: list[str] = []
    if not sub_state or sub_state == "?" or not decision:
        return supersedes
    try:
        # query() por defecto devuelve SOLO decisiones activas
        previas = ckg.query({"type": "decision"})
    except Exception:
        return supersedes
    for old in previas:
        meta = old.get("metadata", {}) or {}
        if meta.get("sub_state") != sub_state:
            continue
        old_decision = meta.get("decision", "")
        if old_decision and old_decision != decision:
            old_id = old.get("_id", "")
            if old_id:
                supersedes.append(old_id)
    return supersedes


@as_side_effect(scope=SCOPE_DECISION)
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

        # CICLO DE VIDA: detectar si esta decision reemplaza a una anterior
        # sobre el mismo sub_state (PO cambio de opinion).
        supersedes = _detect_superseded(ckg, decision, sub_state)

        node = {
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
        }
        if supersedes:
            # La decision nueva reemplaza a la(s) anterior(es): save_nodes
            # degradara a las previas automaticamente.
            node["supersedes"] = supersedes

        result = ckg.save_nodes([node])
        result.setdefault("supersedes_detected", supersedes)
        return {"ok": True, "captured": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e), "captured": False}


def register(engine: Any) -> None:
    engine.register("capture_decision", capture_decision)
