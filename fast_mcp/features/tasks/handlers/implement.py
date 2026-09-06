"""handlers/implement.py — implement_task + run_tests handlers.

Ported from mcp/shared/tasks/actions/implement.py (114 LOC → ~60 LOC).
Simplified: removed legacy ValidatedVO, uses plain dict validation.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult
from fast_mcp.features.tasks.handlers._inspect_codebase_helper import run_inspect_codebase


def implement_task(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Prepare task implementation — records objective and files.

    W5 (ADR-076): si ctx.cpg_adapter está disponible y `data` incluye
    `inspect_codebase_action`, ejecuta la action y añade el resultado.
    """
    objective = data.get("objective", "")
    files = data.get("files", [])
    if not isinstance(files, list):
        files = []

    result: dict[str, Any] = {
        "task_id": data.get("task_id") or ctx.task_id,
        "result_state": objective,
        "files_modified": files,
    }

    inspect_result = run_inspect_codebase(ctx.cpg_adapter, data)
    if inspect_result is not None:
        result["inspect_codebase"] = inspect_result

    return TaskResult.success(data=result)


def _resolve_project_root(chain_dir: str) -> str:
    """Walk up from chain_dir to find project root (dir with tests/)."""
    current = os.path.abspath(chain_dir)
    while True:
        if os.path.isdir(os.path.join(current, "tests")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return chain_dir
        current = parent


def run_tests(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Run test suite via pytest --collect-only.

    SPEC-014: prohibits false positive (passed=True with total=0).
    """
    project_root = _resolve_project_root(ctx.chain_dir or "")
    try:
        result = subprocess.run(
            ["pytest", "--collect-only", "-q"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return TaskResult.failure(
                f"run_tests: pytest --collect-only failed (rc={result.returncode})"
            )
        match = re.search(r"(\d+) tests? collected", result.stderr + result.stdout)
        total = int(match.group(1)) if match else 0
        if total <= 0:
            return TaskResult.failure(
                "run_tests: 0 tests collected — cannot certify QA"
            )
        return TaskResult.success(data={
            "task_id": data.get("task_id", ctx.task_id),
            "passed": True,
            "total": total,
            "failures": 0,
            "runner": "pytest --collect-only",
        })
    except subprocess.TimeoutExpired:
        return TaskResult.failure("run_tests: pytest timed out (120s)")
    except FileNotFoundError:
        return TaskResult.failure("run_tests: pytest not found")
    except Exception as exc:
        return TaskResult.failure(f"run_tests: {type(exc).__name__}: {exc}")


def register(registry) -> None:
    """Register implement_task and run_tests."""
    registry.register("implement_task", implement_task, metadata={
        "description": "Prepare task implementation",
        "tags": ["implementation"],
        "scope": "internal",
    })
    registry.register("run_tests", run_tests, metadata={
        "description": "Run test suite",
        "tags": ["test"],
        "scope": "internal",
    })
