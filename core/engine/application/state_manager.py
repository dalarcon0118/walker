"""application/state_manager.py — Gestión de WorkflowState.

SRP: Responsabilidad única = cargar, guardar y operar sobre el estado
del workflow (WorkflowState). Delega en WorkflowStateRepository.
"""
from __future__ import annotations

from core.engine.engine import load_state, save_state
from core.engine.domain.entities import WorkflowState


def ensure_state(chain_dir: str) -> dict:
    """Carga estado como dict. Sin chain_dir → ERROR.

    PRINCIPIO: no retorna defaults inventados. El estado vacío solo se
    produce si realmente no hay sprint persistido.
    """
    if not chain_dir:
        raise ValueError("chain_dir vacío — no se puede resolver el estado")
    return load_state(chain_dir).to_dict()


def store_contract_data(
    chain_dir: str | None,
    task_id: str | None,
    target_state: str,
    data: dict,
) -> None:
    """Almacena contract data en WorkflowState.walker.contract_data."""
    if not chain_dir or not data:
        return
    state = load_state(chain_dir)
    cd = state.walker.setdefault("contract_data", {})
    if task_id:
        cd.setdefault(target_state, {})[task_id] = data
    else:
        cd[target_state] = data
    save_state(state, chain_dir)


def log_decision(
    chain_dir: str,
    from_state: str,
    gate: str | None,
    task_id: str | None = None,
    reason: str = "",
) -> None:
    """Registra una decisión en WorkflowState.walker.decision_log."""
    if not chain_dir:
        return
    state = load_state(chain_dir)
    log = state.walker.setdefault("decision_log", [])
    log.append(
        {
            "from": from_state,
            "gate": gate,
            "task_id": task_id,
            "reason": reason,
            "at": _now(),
        }
    )
    save_state(state, chain_dir)


def apply_state_transition(
    state: WorkflowState | dict,
    target: str,
    old_state: str,
    gate: str,
    reason: str,
    chain_dir: str | None,
    task_id: str | None,
) -> WorkflowState:
    """Aplica transición entre nodos y persiste."""
    if isinstance(state, dict):
        sprint_state = WorkflowState.from_dict(state)
    else:
        sprint_state = state

    graph = load_workflow(chain_dir)
    from core.engine.domain.services import TransitionService

    sprint_state = TransitionService.apply(sprint_state, graph, target)

    if reason:
        sprint_state = sprint_state.with_context("last_reason", reason)
    if task_id:
        sprint_state = sprint_state.with_context("task_id", task_id)
    sprint_state = sprint_state.with_context("chain_dir", chain_dir)
    save_state(sprint_state, chain_dir)

    print(f"  [{old_state}] -> [{target}] (gate: {gate})")
    if reason:
        print(f"    Razon: {reason}")
    print()
    return sprint_state


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
