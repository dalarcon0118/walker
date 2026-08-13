"""features/main/app.py — AppComposition y WalkerApp.

Expone la API pública de la aplicación compuesta por features/main.
"""

from __future__ import annotations

from typing import Any

from features.main.bootstrap import AppComposition
from shared.common.shared_domain.models import ExecutionContext


class WalkerApp:
    """API pública de Walker.

    Encapsula la composición completa de la aplicación y expone
    las operaciones de workflow, tasks, y MCP tools.
    """

    def __init__(self, composition: AppComposition) -> None:
        self._composition = composition

    @property
    def workflow(self):
        return self._composition.workflow

    @property
    def task_registry(self):
        return self._composition.task_registry

    @property
    def side_effect_engine(self):
        return self._composition.side_effect_engine

    def _make_ctx(self, chain_dir: str, task_id: str = "") -> ExecutionContext:
        return ExecutionContext.create(
            session_id=str(chain_dir).split("/")[-1] if chain_dir else "",
            chain_dir=chain_dir,
            task_id=task_id or "",
        )

    def status(self, project_id: str, repo_path: str = "", sprint_id: str = "") -> dict:
        ctx = self._make_ctx(project_id)
        return self._composition.workflow.status(ctx)

    def submit(self, chain_dir: str, fields: dict, **kwargs) -> dict:
        ctx = self._make_ctx(chain_dir, kwargs.pop("task_id", ""))
        return self._composition.workflow.submit(ctx, fields=fields, **kwargs)

    def transition(self, chain_dir: str, gate_name: str = "", **kwargs) -> dict:
        ctx = self._make_ctx(chain_dir, kwargs.pop("task_id", ""))
        return self._composition.workflow.transition(ctx, gate_name, **kwargs)

    def prepare(self, project_path: str, args_flags: dict | None = None) -> dict:
        from features.main.cli_interface.application.project_use_cases import PrepareProjectUseCase
        return PrepareProjectUseCase.execute(project_path, args_flags or {})
