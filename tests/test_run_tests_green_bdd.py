"""BDD: `run_tests` ejecuta la suite y devuelve verde/rojo real.

Antes usaba `pytest --collect-only`: decía passed=True sin ejecutar nada, así
que el cierre "verde" no significaba nada.
"""

from __future__ import annotations

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext
from fast_mcp.features.tasks.handlers import implement as impl


class _Result:
    def __init__(self, rc: int, out: str) -> None:
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


def _ctx() -> ExecutionContext:
    return ExecutionContext.create(chain_dir="/tmp", session_id="s1")


def test_suite_verde(monkeypatch):
    monkeypatch.setattr(impl.subprocess, "run", lambda *a, **k: _Result(0, "3 passed in 0.10s"))

    out = impl.run_tests(_ctx(), {})

    assert out.ok and out.data["passed"] is True
    assert out.data["total"] == 3 and out.data["failures"] == 0


def test_suite_roja(monkeypatch):
    monkeypatch.setattr(impl.subprocess, "run", lambda *a, **k: _Result(1, "1 failed, 2 passed in 0.2s"))

    out = impl.run_tests(_ctx(), {})

    assert out.ok is False
    assert "SUITE ROJA" in out.error


def test_cero_tests_no_certifica(monkeypatch):
    monkeypatch.setattr(impl.subprocess, "run", lambda *a, **k: _Result(0, "no tests ran in 0.01s"))

    out = impl.run_tests(_ctx(), {})

    assert out.ok is False
    assert "0 tests" in out.error
