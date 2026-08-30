"""use_cases/_helpers.py — Helpers de módulo de los casos de uso (SRP)."""
from __future__ import annotations

def _flush_events(
    events: list[DomainEvent],
    event_log: Any,
    event_dispatcher: Any,
    chain_dir: str | None,
    task_id: str | None,
) -> None:
    """Persiste y dispara eventos acumulados.

    SRP: Unica funcion compartida para persistir eventos en EventLog
    y dispararlos via EventDispatcher. DRY: evita duplicar en cada UseCase.

    Nota: event_log/event_dispatcher tipados como Any para evitar
    import circular (events.py <- workflow_engine -> use_cases.py -> events.py).
    """
    if event_log and chain_dir:
        event_log.set_chain_dir(chain_dir)
        for event in events:
            event_log.append_and_flush(event)
    if event_dispatcher:
        event_dispatcher.dispatch_many(events, chain_dir, task_id)



def _sub_state_decision_prefix(sub_state_key: str) -> str:
    """Deriva el prefijo de la gate de decision para un sub-estado user_input.

    Convencion declarativa: un sub-estado con decision binaria
    (approve/revise) expone gates XOR de la forma
    '<prefix>_approved' y '<prefix>_revised'. El prefijo es el nombre
    del sub-estado en snake_case.

    Ejemplos:
        PLAN_APPROVAL  -> plan_approval (gates plan_approval_approved/revised)
        CLOSE_CONFIRM  -> close_confirm_approved / close_confirm_revised
        TASK_REVIEW    -> task_review_approved / task_review_revised
    """
    import re

    s = sub_state_key or ""
    # CamelCase/SCREAMING -> snake_case: PLAN_APPROVAL -> plan_approval
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = s.replace("-", "_")
    return s.lower()



def _sprint_id_of(chain_dir: str) -> str:
    """Deriva el spring_id del chain_dir (nombre del directorio)."""
    import os

    return os.path.basename(os.path.normpath(chain_dir or ""))


# Mapa de decisiones ESPECIFICAS por sub-estado (override de la convencion).
# Un sub-estado puede declarar aqui como mapea cada decision a su gate.
# Las gates de aprobacion de cada nodo tienen nombres propios (design_approved,
# analysis_approved, task_complete) que NO se derivan por la convencion
# <prefix>_approved — por eso se declaran explicitamente aqui.
_DECISION_GATE_OVERRIDES: dict[str, dict[str, str]] = {
    # TASK_REVIEW: approve -> next_task (o all_done si es la ultima), refine -> refine_task
    "TASK_REVIEW": {
        "approve": "next_task",
        "refine": "refine_task",
        "all_done": "all_done",
    },
    # DESIGN_REVIEW: approve -> design_approved (gate propia del nodo DESIGN)
    "DESIGN_REVIEW": {
        "approve": "design_approved",
        "reject": "design_needs_refine",
        "refine": "design_needs_refine",
    },
    # ANALYZE.REVIEW: approve -> analysis_approved (KNOWLEDGE_REVIEW fue
    # fusionado en REVIEW — SPEC-005)
    "REVIEW": {
        "approve": "analysis_approved",
        "reject": "needs_refine",
        "refine": "needs_refine",
    },
    # IMPLEMENT gates: user gates de implementacion
    "USER_GATE": {
        "approve": "implementation_done",
        "reject": "needs_refine",
        "cancel": "cancel",
    },
    "CODE_REVIEW_RESULT": {
        "approve": "implementation_done",
        "reject": "needs_refine",
        "refine": "needs_refine",
    },
    "REPORT_GATE": {
        "approve": "implementation_done",
        "reject": "needs_refine",
        "refine": "needs_refine",
    },
    # VERIFY gates: QA y PO_ACCEPT -> task_complete
    "QA": {
        "approve": "task_complete",
        "reject": "needs_refine",
        "refine": "needs_refine",
    },
    "PO_ACCEPT": {
        "approve": "task_complete",
        "reject": "needs_refine",
        "refine": "needs_refine",
    },
    # CLOSE_CONFIRM: close_sprint
    "CLOSE_CONFIRM": {
        "approve": "close_sprint",
        "reject": "back_to_active",
    },
    # CONFIRM_GATE (REFINE): approve -> dejar que ROUTE (auto) resuelva el
    # target del diagnostico dinamicamente (NO mapear a una gate fija);
    # modify -> volver a DIAGNOSE, cancel -> volver a sprint.
    "CONFIRM_GATE": {
        "approve": "",
        "modify": "reroute_design",
        "cancel": "reroute_sprint",
    },
}



def _resolve_decision_gate(
    state: WorkflowState,
    workflow: WorkflowGraph,
    decision: str,
    fields: dict,
) -> str:
    """Mapea una decision de user_input a la gate XOR a disparar.

    Orden de resolucion:
      1. Override declarativo por sub-estado (_DECISION_GATE_OVERRIDES):
         - TASK_REVIEW.approve -> next_task (o all_done si es la ultima tarea)
         - TASK_REVIEW.refine  -> refine_task
      2. Convencion binaria generica: approve/confirm/yes/proceed/accept
         -> <prefix>_approved; revise/reject/no/back/cancel -> <prefix>_revised.

    Args:
        state: Estado del workflow (sub-estado actual).
        workflow: Grafo del workflow (para contar specs restantes).
        decision: Valor del campo decision (approve, revise, refine, ...).
        fields: Campos completos del submit (para contexto adicional).

    Returns:
        Nombre de la gate a disparar, o "" si no hay mapeo.
    """
    decision = (decision or "").lower().strip()
    sub_key = state.current_sub_state
    if not decision:
        return ""

    # 1. Override declarativo
    override = _DECISION_GATE_OVERRIDES.get(sub_key)
    if override and decision in override:
        gate = override[decision]
        # TASK_REVIEW.approve: si es la ultima tarea, all_done en vez de next_task
        if sub_key == "TASK_REVIEW" and decision == "approve":
            total = 0
            current = 0
            # El conteo de specs (all_done en TASK_REVIEW) depende del
            # DeltaGraph, que es INCUMBENCIA DEL HOST (ADR-WALKER-067): el
            # engine NO consulta spring. El host provee total/current en
            # state.context.spring; si no están, se conserva next_task.
            try:
                ctx_state = state.context.get("spring", {}) or {}
                total = int(ctx_state.get("total_specs", 0) or 0)
                current = int(ctx_state.get("current_spec_index", 0) or 0)
            except Exception:
                pass
            if total > 0 and current >= total - 1:
                gate = "all_done"
        return gate

    # 2. Convencion binaria generica
    prefix = _sub_state_decision_prefix(sub_key)
    positive = {"approve", "confirm", "yes", "proceed", "accept"}
    negative = {"revise", "reject", "no", "back", "cancel"}
    if decision in positive:
        return f"{prefix}_approved"
    if decision in negative:
        return f"{prefix}_revised"
    return ""


_DECISION_GATE_OVERRIDES: dict[str, dict[str, str]] = {
    # TASK_REVIEW: approve -> next_task (o all_done si es la ultima), refine -> refine_task
    "TASK_REVIEW": {
        "approve": "next_task",
        "refine": "refine_task",
        "all_done": "all_done",
    },
    # DESIGN_REVIEW: approve -> design_approved (gate propia del nodo DESIGN)
    "DESIGN_REVIEW": {
        "approve": "design_approved",
        "reject": "design_needs_refine",
        "refine": "design_needs_refine",
    },
    # ANALYZE.REVIEW: approve -> analysis_approved (KNOWLEDGE_REVIEW fue
    # fusionado en REVIEW — SPEC-005)
    "REVIEW": {
        "approve": "analysis_approved",
        "reject": "needs_refine",
        "refine": "needs_refine",
    },
    # IMPLEMENT gates: user gates de implementacion
    "USER_GATE": {
        "approve": "implementation_done",
        "reject": "needs_refine",
        "cancel": "cancel",
    },
    "CODE_REVIEW_RESULT": {
        "approve": "implementation_done",
        "reject": "needs_refine",
        "refine": "needs_refine",
    },
    "REPORT_GATE": {
        "approve": "implementation_done",
        "reject": "needs_refine",
        "refine": "needs_refine",
    },
    # VERIFY gates: QA y PO_ACCEPT -> task_complete
    "QA": {
        "approve": "task_complete",
        "reject": "needs_refine",
        "refine": "needs_refine",
    },
    "PO_ACCEPT": {
        "approve": "task_complete",
        "reject": "needs_refine",
        "refine": "needs_refine",
    },
    # CLOSE_CONFIRM: close_sprint
    "CLOSE_CONFIRM": {
        "approve": "close_sprint",
        "reject": "back_to_active",
    },
    # CONFIRM_GATE (REFINE): approve -> dejar que ROUTE (auto) resuelva el
    # target del diagnostico dinamicamente (NO mapear a una gate fija);
    # modify -> volver a DIAGNOSE, cancel -> volver a sprint.
    "CONFIRM_GATE": {
        "approve": "",
        "modify": "reroute_design",
        "cancel": "reroute_sprint",
    },
}
