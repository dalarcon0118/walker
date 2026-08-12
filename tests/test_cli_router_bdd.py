"""Tests BDD (pytest-bdd) — CLI Router entry point.

Escenarios: bdd/features/cli_router.feature
"""

from __future__ import annotations

import sys
from io import StringIO
from unittest.mock import patch

import pytest
from pytest_bdd import given, scenarios, then

scenarios("bdd/features/cli_router.feature")


@pytest.fixture
def cli_ctx() -> dict:
    return {}


@pytest.fixture(autouse=True)
def _restore_global_registry(cli_ctx):
    """Restaura el registro global registrado por los given que montan sprint."""
    yield
    reg_ctx = cli_ctx.get("_reg_ctx")
    if reg_ctx is not None:
        reg_ctx.__exit__(None, None, None)
        cli_ctx["_reg_ctx"] = None
        from features.engine.bootstrap import reset_module_registry

        reset_module_registry()
        from features.cli_interface import mcp_adapter

        mcp_adapter._SERVER = None


def _run_main(args: list[str]) -> tuple[int, str, str]:
    captured_out = StringIO()
    captured_err = StringIO()
    with (
        patch.object(sys, "stdout", captured_out),
        patch.object(sys, "stderr", captured_err),
        patch.object(sys, "argv", ["walker"] + args),
    ):
        from features.cli_interface import main

        rc = main(args)
    return rc, captured_out.getvalue(), captured_err.getvalue()


@given("que ejecuto main con []")
def _main_empty(cli_ctx: dict):
    rc, out, err = _run_main([])
    cli_ctx["rc"] = rc
    cli_ctx["out"] = out
    cli_ctx["err"] = err


@given('que ejecuto main con ["status"]')
def _main_status(cli_ctx: dict, tmp_path, monkeypatch):
    # Fix de test dependiente del entorno: `walker status` desde el CWD real
    # mostraba "no active sprint" (el repo no tiene sprint activo) y la salida
    # no contenía "workflow" — fallo por estado del repo, no del código.
    # Se monta un sprint activo temporal y se cambia el CWD para que
    # resolve_from_registry lo encuentre.
    from tests.conftest import make_project_with_active_sprint

    tmp, sprint_dir, reg_ctx = make_project_with_active_sprint(tmp_path)
    monkeypatch.chdir(tmp)
    cli_ctx["_reg_ctx"] = reg_ctx
    rc, out, err = _run_main(["status"])
    cli_ctx["rc"] = rc
    cli_ctx["out"] = out
    cli_ctx["err"] = err


@given('que ejecuto main con ["POST", "submit", \'{"status":"submitted"}\']')
def _main_submit(cli_ctx: dict):
    rc, out, err = _run_main(["POST", "submit", '{"status":"submitted"}'])
    cli_ctx["rc"] = rc
    cli_ctx["out"] = out
    cli_ctx["err"] = err


@given('que ejecuto main con ["GET", "status"]')
def _main_get_status(cli_ctx: dict):
    rc, out, err = _run_main(["GET", "status"])
    cli_ctx["rc"] = rc
    cli_ctx["out"] = out
    cli_ctx["err"] = err


@given('que ejecuto main con ["walker/status"]')
def _main_walker_status(cli_ctx: dict):
    rc, out, err = _run_main(["walker/status"])
    cli_ctx["rc"] = rc
    cli_ctx["out"] = out
    cli_ctx["err"] = err


@given('que ejecuto main con ["task", "kg_search", "--query", "ckg"]')
def _main_task(cli_ctx: dict):
    rc, out, err = _run_main(["task", "kg_search", "--query", "ckg"])
    cli_ctx["rc"] = rc
    cli_ctx["out"] = out
    cli_ctx["err"] = err


@given('que ejecuto main con ["submit", \'{"field":"value"}\']')
def _main_json_positional(cli_ctx: dict):
    rc, out, err = _run_main(["submit", '{"field":"value"}'])
    cli_ctx["rc"] = rc
    cli_ctx["out"] = out
    cli_ctx["err"] = err


@given('que ejecuto main con ["kg_search?index=ckg"]')
def _main_query_params(cli_ctx: dict):
    rc, out, err = _run_main(["kg_search?index=ckg"])
    cli_ctx["rc"] = rc
    cli_ctx["out"] = out
    cli_ctx["err"] = err


@given('que ejecuto main con ["no_existe"]')
def _main_unknown(cli_ctx: dict):
    rc, out, err = _run_main(["no_existe"])
    cli_ctx["rc"] = rc
    cli_ctx["out"] = out
    cli_ctx["err"] = err


@then("el exit code es 0")
def _assert_exit_0(cli_ctx: dict):
    assert cli_ctx["rc"] == 0, f"exit_code={cli_ctx['rc']}, out={cli_ctx['out']!r}"


@then("el exit code es 1")
def _assert_exit_1(cli_ctx: dict):
    assert cli_ctx["rc"] == 1, f"exit_code={cli_ctx['rc']}, out={cli_ctx['out']!r}"


@then('la salida contiene "Walker REST protocol"')
def _assert_help(cli_ctx: dict):
    combined = cli_ctx["out"] + cli_ctx["err"]
    assert "Walker REST protocol" in combined, (
        f"'Walker REST protocol' not in output:\n{combined}"
    )


@then('la salida contiene "workflow"')
def _assert_workflow(cli_ctx: dict):
    combined = cli_ctx["out"] + cli_ctx["err"]
    assert "workflow" in combined, f"'workflow' not in output:\n{combined}"


@then('la salida contiene "no registrada"')
def _assert_not_registered(cli_ctx: dict):
    combined = cli_ctx["out"] + cli_ctx["err"]
    assert "no registrada" in combined, f"'no registrada' not in output:\n{combined}"
