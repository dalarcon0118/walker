"""infrastructure/default_workflow_io.py — IWorkflowIO adapter.

Executes gate validation scripts via subprocess with timeout.
Safe interpolation of <chain_dir> placeholders.
Fail-open: errors return {exit_code: -1, stderr: ...}, never raise.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from typing import Any

from ..domain.ports import IWorkflowIO

_SCRIPT_TIMEOUT = 120


class DefaultWorkflowIO(IWorkflowIO):
    """Default IWorkflowIO: subprocess execution with timeout.

    No dependencies on legacy code. Pure stdlib.
    """

    def run_script(
        self, script: str, chain_dir: str | None = None
    ) -> dict[str, Any]:
        """Execute a gate validation script with safe interpolation.

        <chain_dir> is replaced with shlex.quote(chain_dir).
        DELTA_CHAIN_DIR env var is set for the subprocess.
        Returns {exit_code, stdout, stderr}. Never raises.
        """
        env = os.environ.copy()
        cmd = script

        if chain_dir:
            cmd = cmd.replace("<chain_dir>", shlex.quote(chain_dir))
            cmd = cmd.replace("<chain>", shlex.quote(chain_dir))
            env["DELTA_CHAIN_DIR"] = chain_dir

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=_SCRIPT_TIMEOUT,
                env=env,
            )
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
            }
        except subprocess.TimeoutExpired:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Timeout after {_SCRIPT_TIMEOUT}s: {script[:200]}",
            }
        except Exception as exc:  # noqa: BLE001 - fail-open
            return {"exit_code": -1, "stdout": "", "stderr": str(exc)}

    def eprint(self, *args: Any, **kwargs: Any) -> None:
        """Print to stderr (neutral output interface)."""
        print(*args, file=sys.stderr, **kwargs)
