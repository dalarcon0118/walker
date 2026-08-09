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

Result pattern (dry-python/returns): los cores con @safe nunca lanzan; los
wrappers degradan con fallback explícito + log (convention-001).
"""
from __future__ import annotations
import logging
from typing import Any
from returns.pipeline import is_successful
from returns.result import safe
from libs.shared_domain import ExecutionContext
from features.engine.domain.side_effect_contract import as_side_effect, SCOPE_DECISION

logger = logging.getLogger(__name__)


@safe
def _detect_superseded_core(ckg, decision: str, sub_state: str) -> list[str]:
    """Core puro: detecta decisiones activas previas sobre el sub_state (puede lanzar)."""
    supersedes: list[str] = []
    if not sub_state or sub_state == "?" or not decision:
        return supersedes
    # query() por defecto devuelve SOLO decisiones activas
    previas = ckg.query({"type": "decision"})
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


def _detect_superseded(ckg, decision: str, sub_state: str) -> list[str]:
    """Detecta decisiones ACTIVAS previas sobre el mismo sub_state que esta reemplaza.

    Regla: una decision nueva sobre el mismo punto de decision (sub_state)
    con VALOR DISTINTO al previo indica que el PO cambio de opinion → la
    nueva reemplaza a la anterior. Mismo valor = reconfirmacion (no supersede).
    Solo se consideran decisiones ACTIVAS (las ya superseded pertenecen a la
    historia y no vuelven a reemplazarse).
    """
    result = _detect_superseded_core(ckg, decision, sub_state)
    if is_successful(result):
        return result.value_or([])
    logger.warning(
        "decision_capture: deteccion de superseded fallo (%s: %s)",
        type(result.failure()).__name__, result.failure(),
    )
    return []


@safe
def _read_decision_from_state_core(chain_dir: str) -> dict:
    """Core puro: lee la decision del estado del workflow (puede lanzar)."""
    from features.engine.infrastructure.repositories import WorkflowStateRepository
    state = WorkflowStateRepository().load(chain_dir)
    ss_data = state.get_sub_state_data(state.current_sub_state)
    return {
        "decision": ss_data.fields.get("decision", "") or "",
        "feedback": ss_data.fields.get("feedback", "") or "",
        "evidence": ss_data.fields.get("evidence", "") or "",
        "sub_state": f"{state.current_node}.{state.current_sub_state}",
    }


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
        state_result = _read_decision_from_state_core(chain_dir)
        if is_successful(state_result):
            state_data = state_result.value_or({})
            decision = state_data.get("decision", "")
            rationale = rationale or state_data.get("feedback", "") or state_data.get("evidence", "")
            sub_state = state_data.get("sub_state", "?")
        else:
            logger.warning(
                "decision_capture: no se pudo leer la decision del estado (%s: %s)",
                type(state_result.failure()).__name__, state_result.failure(),
            )

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
        logger.warning(
            "decision_capture: capture_decision fallo (%s: %s)", type(e).__name__, e,
        )
        return {"ok": False, "error": str(e), "captured": False}


def register(engine: Any) -> None:
    engine.register("capture_decision", capture_decision)
