"""
engine — Workflow engine puro.

SRP: Orquestar el flujo de trabajo entre nodos y sub-estados.
PRINCIPIO: NO hace IO directo. Delega en infrastructure/repositories.py
PRINCIPIO: NO tiene fallbacks silenciosos. Si falta un archivo, ERROR.
PRINCIPIO: NO tiene compatibilidad legacy (no emergency_workflow,
           presprint_workflow, reconstruct_walker_state, ni defaults efímeros).

Refactorizado: las responsabilidades se delegaron en:
    - application/workflow_loader.py   → carga y conversión de workflow
    - application/state_manager.py     → gestión de WorkflowState
"""
from __future__ import annotations

from .domain.models import WorkflowState, WorkflowGraph
from .infrastructure.repositories import WorkflowRepository, WorkflowStateRepository


# ═════════════════════════════════════════════════════════════════════════
# Repositorios (IO controlado)
# ═════════════════════════════════════════════════════════════════════════


def load_workflow(workflow_repo: WorkflowRepository) -> WorkflowGraph:
    """Carga el workflow desde el repo configurado. ERROR si no existe.

    Args:
        workflow_repo: WorkflowRepository configurado con el workflow_path
                       resuelto por el main/composition root.

    Raises:
        FileNotFoundError: si el workflow_path no existe.
    """
    graph = workflow_repo.load()
    if not graph or not graph.nodes:
        raise FileNotFoundError(
            f"Workflow vacío en '{workflow_repo._workflow_path}'"
        )
    return graph


def load_state(chain_dir: str) -> WorkflowState:
    """Carga el estado del sprint. Retorna WorkflowState vacío si no existe."""
    if not chain_dir:
        raise ValueError("chain_dir vacío")
    return WorkflowStateRepository().load(chain_dir)


def save_state(state: WorkflowState, chain_dir: str) -> None:
    """Persiste el estado del sprint."""
    if not chain_dir:
        raise ValueError("chain_dir vacío")
    WorkflowStateRepository().save(chain_dir, state)


# ═════════════════════════════════════════════════════════════════════════
# Lógica de dominio pura
# ═════════════════════════════════════════════════════════════════════════


def check_blocked_state(
    state: WorkflowState,
    graph: WorkflowGraph,
    strategy: str = "wait",
    payload: dict | None = None,
) -> dict:
    """Verifica si el workflow está bloqueado y aplica estrategia de resolución.

    Helper de integración para el engine actual. Usa BlockResolver para
    resolver timeouts, señales pendientes y bloqueos de gate.

    Usa la monada Result de `returns` explícitamente: el resultado exitoso
    se maneja con `isinstance(..., Success)` y `unwrap()`. No se usan
    excepciones para control de flujo.

    Args:
        state: Estado actual del workflow
        graph: Grafo de workflow
        strategy: Estrategia por defecto si no hay resolución automática
        payload: Datos adicionales para la resolución

    Returns:
        dict con:
            blocked: True si el workflow está bloqueado
            resolved: True si se aplicó una resolución automática
            state: nuevo estado (si fue resuelto)
            action: acción tomada (timeout, signal, force, wait, etc.)
            message: mensaje descriptivo
    """
    from returns.result import Success

    from .domain.services.block_resolver import BlockResolver

    if not BlockResolver.is_blocked(state):
        return {"blocked": False, "resolved": False, "state": state.to_dict()}

    timeout_result = _check_timeout_blocked(state, graph)
    if timeout_result.get("resolved"):
        return timeout_result

    signal_result = _check_signal_blocked(state, graph)
    if signal_result.get("resolved"):
        return signal_result

    resolve_result = BlockResolver.resolve(
        state, strategy, payload or {}, graph=graph
    )
    if isinstance(resolve_result, Success):
        new_state = resolve_result.unwrap()
        return {
            "blocked": True,
            "resolved": True,
            "state": new_state.to_dict(),
            "action": strategy,
            "message": f"Block resolved with strategy '{strategy}'",
        }

    return {
        "blocked": True,
        "resolved": False,
        "state": state.to_dict(),
        "action": strategy,
        "message": f"Block could not be resolved with strategy '{strategy}'",
    }


def _check_timeout_blocked(state: WorkflowState, graph: WorkflowGraph) -> dict:
    """Verifica si el bloqueo actual tiene timeout expirado."""
    if not state.blocked_at or state.block_timeout_ms <= 0:
        return {"resolved": False}

    from datetime import datetime, timezone
    try:
        block_since = datetime.fromisoformat(state.block_since)
        now = datetime.now(timezone.utc)
        elapsed_ms = (now - block_since).total_seconds() * 1000
    except Exception:
        return {"resolved": False}

    if elapsed_ms < state.block_timeout_ms:
        return {"resolved": False}

    node = graph.nodes.get(state.current_node)
    if not node:
        return {"resolved": False}

    sub_state = node.find_sub_state(state.current_sub_state)
    if not sub_state:
        return {"resolved": False}

    strategy = sub_state.on_timeout or "skip"
    from .domain.services.block_resolver import BlockResolver
    from returns.result import Success

    result = BlockResolver.resolve(
        state, strategy, {"sub_state": state.current_sub_state, "reason": f"timeout after {elapsed_ms:.0f}ms"}, graph=graph
    )
    if isinstance(result, Success):
        new_state = result.unwrap()
        return {
            "resolved": True,
            "blocked": False,
            "state": new_state.to_dict(),
            "action": "timeout",
            "strategy": strategy,
            "message": f"Wait state timed out after {elapsed_ms:.0f}ms. Resolved with '{strategy}'.",
        }
    return {"resolved": False}


def _check_signal_blocked(state: WorkflowState, graph: WorkflowGraph) -> dict:
    """Verifica si el bloqueo actual tiene una señal pendiente que lo resuelva."""
    if not state.blocked_at or not state.pending_signals:
        return {"resolved": False}

    node = graph.nodes.get(state.current_node)
    if not node:
        return {"resolved": False}

    sub_state = node.find_sub_state(state.current_sub_state)
    if not sub_state or not sub_state.waits_for:
        return {"resolved": False}

    for signal in state.pending_signals:
        if signal in sub_state.waits_for:
            strategy = sub_state.on_signal or "auto_advance"
            from .domain.services.block_resolver import BlockResolver
            from returns.result import Success

            result = BlockResolver.resolve(
                state, strategy, {"sub_state": state.current_sub_state, "reason": f"signal '{signal}' received"}, graph=graph
            )
            if isinstance(result, Success):
                new_state = result.unwrap()
                return {
                    "resolved": True,
                    "blocked": False,
                    "state": new_state.to_dict(),
                    "action": "signal",
                    "strategy": strategy,
                    "signal": signal,
                    "message": f"Wait state resumed by signal '{signal}'. Resolved with '{strategy}'.",
                }
    return {"resolved": False}

