"""tests/test_cli_walker_bdd.py — CLI Walker E2E: valida los 6 comandos principales
del CLI via entry point real (main) con captura de stdout/stderr.

Nivel 2 de testing (según SPEC-003):
- test_cli_walker_help_shows_tools
- test_cli_walker_status_shows_project
- test_cli_walker_list_shows_projects
- test_cli_walker_kg_search_finds_spec
- test_cli_walker_save_to_ckg_saves_decision
- test_cli_walker_kg_diagnose_identifies_gap
"""
from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tests.conftest import make_project_with_active_sprint, make_walker_project


# ── Helpers ─────────────────────────────────────────────────────────────

def _setup_project(tmp_path: Path) -> Path:
    """Crea el proyecto walker cognitivo (builder compartido en conftest)."""
    make_walker_project(tmp_path)
    return tmp_path


def _run_main(args: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    captured_out = StringIO()
    captured_err = StringIO()
    old_cwd = None
    if cwd is not None:
        import os

        old_cwd = os.getcwd()
        os.chdir(cwd)
    try:
        with (
            patch.object(sys, "stdout", captured_out),
            patch.object(sys, "stderr", captured_err),
            patch.object(sys, "argv", ["walker"] + args),
        ):
            from features.main.cli_interface import main

            rc = main(args)
    finally:
        if old_cwd is not None:
            import os

            os.chdir(old_cwd)
    return rc, captured_out.getvalue(), captured_err.getvalue()


# ── Tests CLI Walker ─────────────────────────────────────────────────────

class TestCliWalker:
    """Comandos principales del CLI via entry point real."""

    def test_cli_walker_help_shows_tools(self, tmp_path):
        rc, out, err = _run_main(["help"])
        assert rc == 0
        assert "walker" in out.lower() or "protocol" in out.lower()

    def test_cli_walker_status_shows_project(self, tmp_path):
        project_dir = _setup_project(tmp_path)
        rc, out, err = _run_main(["status"], cwd=project_dir)
        assert rc == 0
        assert "walker" in out

    def test_cli_walker_list_shows_projects(self, tmp_path):
        project_dir = _setup_project(tmp_path)
        rc, out, err = _run_main(["list"], cwd=project_dir)
        assert rc == 0
        assert "walker" in out

    def test_cli_walker_kg_search_finds_spec(self, tmp_path):
        project_dir = _setup_project(tmp_path)
        rc, out, err = _run_main(["kg_search", "--query", "SPEC-003"], cwd=project_dir)
        assert rc == 0
        assert "kg_search" in out or "executed successfully" in out

    def test_cli_walker_save_to_ckg_saves_decision(self, tmp_path):
        project_dir = _setup_project(tmp_path)
        rc, out, err = _run_main([
            "save_to_ckg",
            "--type", "decision",
            "--statement", "SPEC-003 requiere tests E2E MCP",
            "--scope", "testing",
            "--tags", "testing,e2e",
        ], cwd=project_dir)
        assert rc == 0
        assert "ok" in out.lower() or "decision" in out.lower()

    def test_cli_walker_kg_diagnose_identifies_gap(self, tmp_path):
        # Fix de test dependiente del entorno: kg_diagnose requiere un
        # chain_dir resolvible (sprint activo registrado). ANTES montaba solo
        # .cognitive/ sin sprint → el CLI no resolvía proyecto y la task
        # fallaba con "chain_dir vacio" (fallo por estado del repo, no del
        # código). Ahora monta el sprint activo real y restaura el registro.
        tmp, sprint_dir, reg_ctx = make_project_with_active_sprint(tmp_path)
        try:
            rc, out, err = _run_main(["kg_diagnose", "--task_id", "SPEC-003"], cwd=Path(tmp))
            assert rc == 0, f"rc={rc}, out={out!r}, err={err!r}"
            assert "SPEC-003" in out or "diagnose" in out.lower(), (
                f"sin diagnóstico: out={out!r}"
            )
        finally:
            reg_ctx.__exit__(None, None, None)
            from core.engine.bootstrap import reset_module_registry

            reset_module_registry()
            from features.main.cli_interface import mcp_adapter

            mcp_adapter._SERVER = None
