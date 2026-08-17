"""features/main/front_controller.py — Front controller común CLI/MCP.

Encapsula los handlers de la aplicación para que CLI y MCP
deleguen en el mismo composition root (features/main).
"""

from __future__ import annotations

from typing import Any

from features.main.container import AppContainer


class FrontController:
    """Front controller que envuelve los handlers de la aplicación.

    CLI y MCP delegan en esta clase para ejecutar operaciones
    a través del composition root único.
    """

    def __init__(self, app: WalkerApp) -> None:
        self._app = app

    def status(self, project_id: str, repo_path: str = "", sprint_id: str = "") -> dict:
        return self._app.status(project_id, repo_path, sprint_id)

    def submit(self, chain_dir: str, fields: dict, **kwargs) -> dict:
        return self._app.submit(chain_dir, fields, **kwargs)

    def transition(self, chain_dir: str, gate_name: str = "", **kwargs) -> dict:
        return self._app.transition(chain_dir, gate_name, **kwargs)

    def prepare(self, project_path: str, args_flags: dict | None = None) -> dict:
        return self._app.prepare(project_path, args_flags)

    def list_projects(self) -> dict:
        return self._app.workflow.list_projects()

    def help(self) -> dict:
        return self._app.workflow.help()
