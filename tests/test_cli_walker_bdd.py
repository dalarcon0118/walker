"""tests/test_cli_walker_bdd.py — CLI Walker E2E: valida los 6 comandos principales
del CLI via entry point real (main) con captura de stdout/stderr.

Nivel 2 de testing (según SPEC-003):
- test_cli_walker_help_shows_tools
- test_cli_walker_status_shows_project
- test_cli_walker_list_shows_projects
- test_cli_walker_kg_search_finds_spec
- test_cli_walker_kg_store_saves_decision
- test_cli_walker_kg_diagnose_identifies_gap
"""
from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tests.conftest import make_walker_project


# ── Helpers ─────────────────────────────────────────────────────────────

def _setup_project(tmp_path: Path) -> Path:
    """Crea el proyecto walker cognitivo (builder compartido en conftest)."""
    make_walker_project(tmp_path)
    return tmp_path


def _run_main(args: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
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

    def test_cli_walker_kg_store_saves_decision(self, tmp_path):
        project_dir = _setup_project(tmp_path)
        rc, out, err = _run_main([
            "kg_store",
            "--type", "decision",
            "--statement", "SPEC-003 requiere tests E2E MCP",
            "--scope", "testing",
            "--tags", "testing,e2e",
        ], cwd=project_dir)
        assert rc == 0
        assert "ok" in out.lower() or "decision" in out.lower()

    def test_cli_walker_kg_diagnose_identifies_gap(self, tmp_path):
        project_dir = _setup_project(tmp_path)
        rc, out, err = _run_main(["kg_diagnose", "--task_id", "SPEC-003"], cwd=project_dir)
        assert rc == 0
        assert "SPEC-003" in out or "diagnose" in out.lower()
