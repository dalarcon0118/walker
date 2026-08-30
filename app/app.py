"""app/app.py — AppComposition y WalkerApp (composition root, movido desde features/main/app.py).

Expone la API pública de la aplicación compuesta por features/main.
"""

from __future__ import annotations

from typing import Any

from app.container import AppContainer
from shared.common.shared_domain.models import ExecutionContext


class WalkerApp:
    """API pública de Walker.

    Encapsula la composición completa de la aplicación y expone
    las operaciones de workflow, tasks, y MCP tools.
    """

    def __init__(self, composition: AppContainer) -> None:
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
        """Status de un proyecto con el contrato público completo.

        Delega en CheckHealthService (Application Service cross-feature,
        ADR-WALKER-048 — mismo contrato que el facade DI "workflow.status")
        — el único contrato que el CLI y el MCP consumen: incluye "checks",
        "sprint" y "next_steps".

        ANTES: devolvía el snapshot crudo del orchestrator (sin "sprint" ni
        "next_steps") y usaba project_id como chain_dir literal → el handler
        resolvía "no active sprint" y el workflow leía
        "<project_id>/.workflow-state.json" (ruta relativa al CWD del server).
        Era el fallo residual test_get_status_resuelve_desde_cwd y el timeout
        del MCP al consultar status desde otro proyecto.

        AHORA: CheckHealthService resuelve el chain_dir real desde el
        DeltaGraph (find_active_sprint / get_sprint_dir) internamente.
        """
        from app.orchestration.check_health_service import CheckHealthService

        return CheckHealthService.execute(
            project_id=project_id,
            repo_path=repo_path or "",
            sprint_id=sprint_id,
        )

    def submit(self, chain_dir: str, fields: dict, **kwargs) -> dict:
        ctx = self._make_ctx(chain_dir, kwargs.pop("task_id", ""))
        return self._composition.workflow.submit(ctx, fields=fields, **kwargs)

    def transition(self, chain_dir: str, gate_name: str = "", **kwargs) -> dict:
        ctx = self._make_ctx(chain_dir, kwargs.pop("task_id", ""))
        return self._composition.workflow.transition(ctx, gate_name, **kwargs)

    def prepare(self, project_path: str, args_flags: dict | None = None) -> dict:
        from app.orchestration.prepare_project_service import PrepareProjectService

        return PrepareProjectService.execute(project_path, args_flags or {})
