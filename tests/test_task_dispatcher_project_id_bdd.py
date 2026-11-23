"""tests/test_task_dispatcher_project_id_bdd.py — el project_id llega al handler.

Bug medido 2026-11-22: el CLI quita `project_id` del payload (para que el
plugin no apunte a otro proyecto) y lo pasa como argumento; el dispatcher lo
guardaba solo en `data["_project_id"]`. `session_bind`/`session_open` leen
`data["project_id"]`, así que escribían `project_id: ""` en el delta de la
sesión y del sprint.

Contrato:
  - `ctx.project_id` y `data["project_id"]` llevan el id de la spec.
  - El id de la spec manda sobre cualquier `project_id` del payload.
  - Sin project_id no se inventa uno (queda vacío).
"""

from __future__ import annotations

from fast_mcp.features.tasks.domain.task_registry import TaskResult
from fast_mcp.features.workflow.application.task_dispatcher import (
    execute_public_task,
)


class _Registry:
    def __init__(self) -> None:
        self.ctx = None
        self.data: dict = {}

    def execute(self, name: str, ctx, data: dict) -> TaskResult:
        self.ctx = ctx
        self.data = data
        return TaskResult.success({"ok": True})


def test_project_id_llega_al_ctx_y_al_data():
    reg = _Registry()

    out = execute_public_task(
        name="session_bind",
        project_id="client-chat",
        chain_dir="/tmp/chain",
        task_data={"session_id": "ses_1", "mode": "new"},
        registry=reg,
    )

    assert out["ok"] is True
    assert reg.ctx.project_id == "client-chat"
    assert reg.data["project_id"] == "client-chat"
    assert reg.data["_project_id"] == "client-chat"


def test_el_project_id_de_la_spec_manda_sobre_el_payload():
    reg = _Registry()

    execute_public_task(
        name="session_bind",
        project_id="client-chat",
        chain_dir="/tmp/chain",
        task_data={"project_id": "otro-proyecto", "_project_id": "otro-proyecto"},
        registry=reg,
    )

    assert reg.data["project_id"] == "client-chat"
    assert reg.data["_project_id"] == "client-chat"


def test_sin_project_id_no_se_inventa():
    reg = _Registry()

    execute_public_task(
        name="help",
        project_id="",
        chain_dir="/tmp/chain",
        task_data={},
        registry=reg,
    )

    assert reg.ctx.project_id == ""
    assert not reg.data.get("project_id")
    assert reg.data["_project_id"] == ""


def test_payload_anidado_no_pisa_el_project_id():
    reg = _Registry()

    execute_public_task(
        name="session_bind",
        project_id="client-chat",
        chain_dir="/tmp/chain",
        task_data={"_payload": {"project_id": "otro"}},
        registry=reg,
    )

    assert reg.data["project_id"] == "client-chat"
