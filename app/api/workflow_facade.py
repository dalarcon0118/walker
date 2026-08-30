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
        return self._orch.submit(ctx, fields=fields or {}, **kwargs)

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
