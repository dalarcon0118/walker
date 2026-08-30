"""api/workflow_facade.py — WorkflowFacade (submit/transition/status/prepare).

Facade pública de las 4 operaciones del workflow que exponen CLI/MCP.
- submit/transition delegan en el OrchestrationService del composition root.
- status/prepare delegan en los orquestadores cross-feature de cli_interface.
"""
from __future__ import annotations

from typing import Any

from shared.common.shared_domain import ExecutionContext


class WorkflowFacade:
    """Facade de workflow: submit, transition, status, prepare."""

    def __init__(self, orch: Any) -> None:
        self._orch = orch

    def _make_ctx(self, chain_dir: str, **kwargs) -> Any:
        return ExecutionContext.create(
            session_id=str(chain_dir).split("/")[-1],
            chain_dir=chain_dir,
            task_id=kwargs.pop("task_id", "") or "",
        )

    def submit(self, chain_dir: str, fields: dict | None = None, **kwargs) -> dict:
        ctx = self._make_ctx(chain_dir, **kwargs)
        gate = self._uncaptured_work_gate(chain_dir)
        if gate:
            return gate
        # El ctx ya transporta task_id/session_id; el engine NO acepta kwargs
        # extra (contrato tipado de ExecutionContext).
        return self._orch.submit(ctx, fields=fields or {})

    def _uncaptured_work_gate(self, chain_dir: str) -> dict | None:
        """Gate host-side de trabajo sin capturar (ADR-WALKER-067 §2.3).

        El motor es anémico y NO conoce el negocio; el HOST valida externamente
        antes de cerrar el sprint: si el sub-estado actual es de cierre del
        sprint (REVIEW/RETRO/CLOSE_CONFIRM/CLOSE) y hay archivos modificados
        sin especificación que los cubra, bloquea el submit (fail-closed) con
        un mensaje accionable. Fail-open si la detección falla (no oculta el
        fallo: el detector devuelve status=ERROR y aquí no se bloquea).
        """
        try:
            state = self._orch._workflow_store.load_state(chain_dir)
            closing = {"REVIEW", "RETRO", "CLOSE_CONFIRM", "CLOSE"}
            if (
                getattr(state, "current_node", "") != "SPRINT"
                or getattr(state, "current_sub_state", "") not in closing
            ):
                return None
            from features.main.workflow.uncaptured_work import (
                UncapturedStatus,
                detect_uncaptured_work,
            )

            result = detect_uncaptured_work(chain_dir)
            if result.status == UncapturedStatus.UNCAPTURED:
                files = ", ".join(result.uncaptured_files[:5])
                return {
                    "ok": False,
                    "action": "gate_blocked",
                    "error_type": "uncaptured_work_gate",
                    "blocked": True,
                    "message": (
                        "GATE DE TRABAJO SIN CAPTURAR: hay archivos modificados "
                        f"sin especificación que los cubra — {files}. Captura el "
                        "trabajo (walker capture_task) antes de cerrar el sprint."
                    ),
                    "uncaptured_files": result.uncaptured_files,
                }
        except Exception:
            pass  # fail-open: si la detección falla, no bloquear el flujo
        return None

    def transition(
        self, chain_dir: str, gate_name: str = "", **kwargs
    ) -> dict:
        ctx = self._make_ctx(chain_dir, **kwargs)
        return self._orch.transition(
            ctx,
            gate_name,
            reason=kwargs.get("reason", ""),
            skip_preconditions=kwargs.get("skip_preconditions", False),
        )

    def status(
        self, project_id: str, repo_path: str = "", sprint_id: str = ""
    ) -> dict:
        from app.orchestration.check_health_service import CheckHealthService

        return CheckHealthService.execute(
            project_id=project_id,
            repo_path=repo_path,
            sprint_id=sprint_id,
        )

    def prepare(self, project_path: str, args_flags: dict) -> dict:
        from app.orchestration.prepare_project_service import PrepareProjectService

        return PrepareProjectService.execute(
            project_path=project_path, args_flags=args_flags
        )
