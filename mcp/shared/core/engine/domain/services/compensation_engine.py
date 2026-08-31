"""domain/services/compensation_engine.py — Motor de compensación de side-effects.

P1: Compensation Handlers BPMN.
Ejecuta acciones de compensación en orden inverso al completarse los sub-estados.

Principio: solo se compensa trabajo YA completado.
Si un side-effect no tiene compensation declarada, se loggea y continúa.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from shared.core.engine.domain.entities import WorkflowState, WorkflowGraph


def _canonical_cg_root(params: dict) -> str:
    """Resuelve la raiz cognitiva CANONICA para la compensacion de CKG.

    Prioridad:
    1. params["chain_dir"] si existe -> proyecto con data/project.json (manifest).
    2. Resolucion desde CWD (mismo criterio que handlers._open_cognitive_graph).
    3. Fallback: sube desde CWD buscando .cognitive.

    Autocontenido (sin path_resolver/locator): el dominio no resuelve el layout
    del host; usa solo convenciones stdlib (walk up por manifest/.cognitive).
    """
    from pathlib import Path

    def _walk_manifest(start: str) -> Path | None:
        p = Path(start or ".")
        for cand in [p, *p.parents]:
            if (cand / "data" / "project.json").is_file():
                return cand
        return None

    chain_dir = params.get("chain_dir") or ""
    resolved = _walk_manifest(chain_dir) or _walk_manifest(".")
    if resolved:
        return str(resolved)
    cwd = Path.cwd()
    for cand in [cwd, *cwd.parents]:
        if (cand / ".cognitive").is_dir():
            return str(cand / ".cognitive")
    return "."


class CompensationEngine:
    """Ejecuta compensaciones de side-effects en orden inverso."""

    @staticmethod
    def execute(
        state: WorkflowState, graph: WorkflowGraph, failed_sub_state: str
    ) -> WorkflowState:
        node = graph.get_node(state.current_node)
        if not node:
            return state

        current_idx = node.sub_state_index(failed_sub_state)
        if current_idx < 0:
            return state

        compensated: list[str] = []
        for i in range(current_idx - 1, -1, -1):
            sub = node.sub_states[i]
            for effect in sub.side_effects or []:
                if effect.compensation:
                    try:
                        CompensationEngine._run(effect)
                        compensated.append(f"{node.node_id}.{sub.key}:{effect.type}")
                    except Exception as e:
                        CompensationEngine._log_failure(sub.key, effect, e)

        if compensated:
            now = datetime.now(timezone.utc).isoformat()
            new_execs = list(state.node_executions)
            new_execs.append(
                type(
                    "NodeExecution",
                    (),
                    {
                        "node": state.current_node,
                        "sub_state": failed_sub_state,
                        "execution_type": "compensation",
                        "action": "compensate",
                        "submitted_fields": {},
                        "task_ok": len(compensated) > 0,
                        "task_result": {"compensated": compensated},
                        "side_effects": [],
                        "error": None
                        if len(compensated) > 0
                        else "some_compensations_failed",
                        "error_type": None if len(compensated) > 0 else "compensation",
                        "gate": "",
                        "completed_at": now,
                        "duration_ms": 0,
                    },
                )()
            )
            return state.__class__(
                current_node=state.current_node,
                current_sub_state=state.current_sub_state,
                sub_state_index=state.sub_state_index,
                sub_state_data=state.sub_state_data,
                history=state.history,
                context=state.context,
                version=state.version,
                updated_at=now,
                node_executions=new_execs,
                conflicts=list(state.conflicts),
                blocked_at=state.blocked_at,
                block_reason=state.block_reason,
                block_since=state.block_since,
                pending_events=list(state.pending_events),
                block_timeout_ms=state.block_timeout_ms,
                pending_signals=list(state.pending_signals),
                signal_history=list(state.signal_history),
            )
        return state

    @staticmethod
    def _run(effect: Any) -> None:
        if not effect or not effect.compensation:
            return
        params = effect.params or {}
        handler_name = effect.compensation
        from ..ports_registry import resolve_port
        from ..ports import ISideEffectEngine
        from returns.pipeline import is_successful

        se = resolve_port(ISideEffectEngine)
        if se is None:
            logger = __import__("logging").getLogger(__name__)
            logger.warning(
                "ISideEffectEngine no registrado: compensacion '%s' omitida para %s",
                handler_name,
                effect.type,
            )
            return
        outcome = se.run_compensation_handler(handler_name, None)
        if outcome is None:
            logger = __import__("logging").getLogger(__name__)
            logger.warning(
                "Compensation handler '%s' not found for %s",
                handler_name,
                effect.type,
            )
            return
        if not is_successful(outcome):
            logger = __import__("logging").getLogger(__name__)
            logger.error(
                "Compensation '%s' failed for %s: %s",
                handler_name,
                effect.type,
                outcome.failure(),
            )

    @staticmethod
    def _log_failure(sub_state: str, effect: Any, error: Exception) -> None:
        import logging

        logging.getLogger(__name__).error(
            "Compensation failed for %s.%s: %s(%s)",
            sub_state,
            effect.type,
            type(error).__name__,
            error,
        )
