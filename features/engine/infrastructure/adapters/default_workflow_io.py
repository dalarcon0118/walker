"""infrastructure/adapters/default_workflow_io.py — Adaptador concreto de IWorkflowIO.

Implementa el puerto IWorkflowIO delegando en libs.infrastructure.utils.
Es el adaptador por defecto; en tests se puede inyectar un mock.
"""
from __future__ import annotations

from typing import Any

from features.engine.domain.ports import IWorkflowIO

from libs.infrastructure.utils import eprint, run_script, _get_sub_state


class DefaultWorkflowIO(IWorkflowIO):
    """Implementación concreta de IWorkflowIO usando libs.infrastructure.utils."""

    def run_script(self, script: str, chain_dir: str | None = None, task_id: str | None = None) -> dict[str, Any]:
        return run_script(script, chain_dir, task_id)

    def eprint(self, *args: Any, **kwargs: Any) -> None:
        eprint(*args, **kwargs)

    def get_sub_state(self, workflow: dict, node_id: str, sub_key: str) -> dict | None:
        return _get_sub_state(workflow, node_id, sub_key)
