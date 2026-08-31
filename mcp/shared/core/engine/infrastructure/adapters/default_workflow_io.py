"""infrastructure/adapters/default_workflow_io.py — Adaptador concreto de IWorkflowIO.

Implementación por defecto del puerto IWorkflowIO para el motor de workflow
genérico. NO depende de legacy.utils: ejecuta scripts vía subprocess con
interpolación segura (<chain>/<chain_dir>/<task_id>) y navega el grafo de
workflow con la estructura estándar.

El motor es propósito-agnóstico: cualquier workflow (no solo sprints) puede
usar este adaptador — valida gates, imprime a stderr y resuelve sub-estados
sin acoplarse a ningún dominio particular.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from typing import Any

from shared.core.engine.domain.ports import IWorkflowIO

_SCRIPT_TIMEOUT = 120


class DefaultWorkflowIO(IWorkflowIO):
    """Implementación concreta de IWorkflowIO (sin dependencias legacy)."""

    def run_script(
        self, script: str, chain_dir: str | None = None, task_id: str | None = None
    ) -> dict[str, Any]:
        """Ejecuta un script de validación de gate con interpolación segura.

        <chain>/<chain_dir> y <task_id> se interpolan con shlex.quote() y se
        exponen como env DELTA_CHAIN_DIR / DELTA_TASK_ID. Devuelve
        {"exit_code", "stdout", "stderr"} (fail-open ante error/timeout).
        """
        env = os.environ.copy()
        if chain_dir:
            script = script.replace("<chain>", shlex.quote(chain_dir)).replace(
                "<chain_dir>", shlex.quote(chain_dir)
            )
            env["DELTA_CHAIN_DIR"] = chain_dir
        if task_id:
            script = script.replace("<task_id>", shlex.quote(task_id))
            env["DELTA_TASK_ID"] = task_id
        try:
            result = subprocess.run(
                script,
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
            return {"exit_code": -1, "stdout": "", "stderr": "Timeout ejecutando: " + script}
        except Exception as exc:  # noqa: BLE001 - fail-open (convention-001)
            return {"exit_code": -1, "stdout": "", "stderr": str(exc)}

    def eprint(self, *args: Any, **kwargs: Any) -> None:
        """Imprime a stderr (interfaz neutra de salida)."""
        print(*args, file=sys.stderr, **kwargs)

    def get_sub_state(self, workflow: dict, node_id: str, sub_key: str) -> dict | None:
        """Resuelve la config de un sub-estado por clave desde el grafo estándar."""
        node = (workflow or {}).get("nodes", {}).get(node_id, {})
        for ss in node.get("sub_states", []):
            if ss.get("key") == sub_key:
                return ss
        return None
