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
    - application/sub_state_executor.py → ejecución de sub-estados
    - interface/presenters.py          → presentación (show_*)
"""
from __future__ import annotations

from typing import Any

from core.engine.domain.ports import IWorkflowIO

from .domain.models import WorkflowState, WorkflowGraph
from .infrastructure.repositories import WorkflowRepository, WorkflowStateRepository


# ═════════════════════════════════════════════════════════════════════════
# Repositorios (IO controlado)
# ═════════════════════════════════════════════════════════════════════════


def load_workflow(chain_dir: str) -> WorkflowGraph:
    """Carga el workflow. ERROR si no existe.

    Raises:
        FileNotFoundError: ruta canónica no encontrada.
    """
    repo = WorkflowRepository()
    graph = repo.load(chain_dir)
    if not graph or not graph.nodes:
        from features.project import ProjectService

        paths = ProjectService.resolve_paths(chain_dir)
        raise FileNotFoundError(
            f"No se encontró workflow.json en '{paths.workflow_json_path}'"
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


def advance_sub_state(state: WorkflowState | dict, graph: WorkflowGraph | dict) -> bool:
    """Avanza al siguiente sub-estado. Pura: modifica state in-memory.

    Acepta tanto WorkflowState/WorkflowGraph como dict/legacy-workflow-dict.
    """
    # Si es dict legacy, operar directamente sobre el dict (sin conversión)
    if isinstance(graph, dict):
        node_id = (
            state.get("current_node", "SPRINT")
            if isinstance(state, dict)
            else state.current_node
        )
        node = graph.get("nodes", {}).get(node_id, {})
        sub_states = node.get("sub_states", [])
        current_idx = (
            state.get("sub_state_index", 0)
            if isinstance(state, dict)
            else state.sub_state_index
        )
        next_idx = current_idx + 1
        if next_idx < len(sub_states):
            next_ss = sub_states[next_idx]
            if isinstance(state, dict):
                state["sub_state_index"] = next_idx
                state["current_sub_state"] = next_ss["key"]
                state["history"].append(f"{node_id}.{next_ss['key']}")
            else:
                # WorkflowState es inmutable — no podemos mutarlo directamente
                # Esta rama no debería ejecutarse (callers usan dict),
                # pero si ocurre, lanzamos error claro
                raise TypeError(
                    "advance_sub_state con WorkflowState requiere AdvanceService. "
                    "Usa: new_state = AdvanceService.advance(state, graph)"
                )
            return True
        return False

    # Con WorkflowGraph, usar AdvanceService (inmutable)
    if isinstance(state, dict):
        state = WorkflowState.from_dict(state)
    from core.engine.domain.services import AdvanceService

    new_state = AdvanceService.advance(state, graph)
    # La funcion retorna bool por compatibilidad, pero el nuevo estado
    # se asigna al caller via reemplazo del dict original
    if new_state is not None:
        # Actualizar el dict in-place para compatibilidad con callers que pasan dict
        if isinstance(state, dict) and not isinstance(state, WorkflowState):
            state.clear()
            state.update(new_state.to_dict())
        return True
    return False


def is_node_complete(
    state: WorkflowState | dict, graph: WorkflowGraph, node_id: str | None = None
) -> bool:
    """Verifica si el nodo está completo."""
    if isinstance(state, dict):
        state = WorkflowState.from_dict(state)
    from core.engine.domain.services import ValidationService

    return ValidationService.is_node_complete(state, graph)


def check_transition_preconditions(
    state: WorkflowState | dict, graph: WorkflowGraph, gate_name: str
) -> list[str]:
    """Valida precondiciones para transición. Retorna lista de errores."""
    if isinstance(state, dict):
        state = WorkflowState.from_dict(state)
    from core.engine.domain.services import ValidationService

    return ValidationService.check_transition_preconditions(state, graph, gate_name)


def validate_gate(
    workflow: dict,
    node_id: str,
    gate: str,
    chain_dir: str | None = None,
    task_id: str | None = None,
    io: IWorkflowIO | None = None,
) -> dict:
    """Valida gate ejecutando su script de validación (run_script).

    Ahora recibe un puerto IWorkflowIO para abstraer la ejecución de scripts.
    """
    if io is None:
        from core.engine.legacy.utils import run_script, _get_sub_state

        class _DefaultIO(IWorkflowIO):
            def run_script(self, script: str, chain_dir: str | None = None, task_id: str | None = None) -> dict[str, Any]:
                return run_script(script, chain_dir, task_id)

            def eprint(self, *args: Any, **kwargs: Any) -> None:
                from core.engine.legacy.utils import eprint
                eprint(*args, **kwargs)

            def get_sub_state(self, workflow: dict, node_id: str, sub_key: str) -> dict | None:
                return _get_sub_state(workflow, node_id, sub_key)

        io = _DefaultIO()

    node = workflow.get("nodes", {}).get(node_id, {})
    gv = node.get("gate_validation", {})
    gate_config = gv.get(gate)
    if not gate_config:
        return {"ok": True, "message": ""}

    script = gate_config["script"]
    valid_exit = gate_config.get("valid_exit", [0])
    error_template = gate_config.get("error_message", "Gate validation failed: $stderr")

    result = io.run_script(script, chain_dir, task_id)
    if result["exit_code"] in valid_exit:
        return {"ok": True, "message": result["stdout"]}

    from string import Template

    msg = Template(error_template).safe_substitute(
        stderr=result["stderr"],
        stdout=result["stdout"],
        exit_code=str(result["exit_code"]),
    )
    return {"ok": False, "message": msg}


def check_blocked_state(
    state: WorkflowState | dict,
    graph: WorkflowGraph | dict,
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

    if isinstance(state, dict):
        state = WorkflowState.from_dict(state)
    if isinstance(graph, dict):
        graph = WorkflowGraph.from_dict(graph)

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

