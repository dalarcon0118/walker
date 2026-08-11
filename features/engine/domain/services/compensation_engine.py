"""domain/services/compensation_engine.py — Motor de compensación de side-effects.

P1: Compensation Handlers BPMN.
Ejecuta acciones de compensación en orden inverso al completarse los sub-estados.

Principio: solo se compensa trabajo YA completado.
Si un side-effect no tiene compensation declarada, se loggea y continúa.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from features.engine.domain.entities import WorkflowState, WorkflowGraph


def _canonical_cg_root(params: dict) -> str:
    """Resuelve la raiz cognitiva CANONICA para la compensacion de CKG.

    Prioridad:
    1. params["chain_dir"] si existe -> resolve_project_root() (manifest
       .cognitive/projects/<pid>/data/project.json).
    2. Resolucion desde CWD (mismo criterio que handlers._open_cognitive_graph).
    3. Fallback: walker_root/.cognitive.

    Evita usar "" o "." crudo: con chain_dir vacio el storage relativo
    (data/ckg.json.gz) resolveria contra el CWD, escribiendo las memorias
    en <repo>/data en vez de .cognitive/projects/<pid>/data.
    """
    try:
        from libs.path_resolver import get_cognitive_dir, resolve_project_root

        chain_dir = params.get("chain_dir") or ""
        if chain_dir:
            try:
                resolved = resolve_project_root(chain_dir)
                if resolved:
                    return resolved
            except Exception:
                pass
        try:
            resolved = resolve_project_root(".")
            if resolved:
                return resolved
        except Exception:
            pass
        return str(get_cognitive_dir())
    except Exception:
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
        try:
            if (
                effect.type == "update_cpg"
                and effect.compensation == "restore_cpg_snapshot"
            ):
                params = effect.params or {}
                snapshot = params.get("snapshot_id")
                if snapshot:
                    from libs.infrastructure.utils import run_script

                    run_script(f"walker cpg_restore {snapshot}", None, None)
            elif effect.type == "save_ckg" and effect.compensation == "rollback_ckg":
                params = effect.params or {}
                doc_id = params.get("doc_id")
                if doc_id:
                    from features.engine.bootstrap import get_module_service

                    cg = get_module_service("cognitive_graph")(
                        _canonical_cg_root(params)
                    )
                    cg.delete("ckg", doc_id)
        except Exception:
            pass

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
