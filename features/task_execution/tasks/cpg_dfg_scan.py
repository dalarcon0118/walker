"""cpg_dfg_scan — DFG security scan asíncrono vía CPG.

No bloquea al agente: lanza scan_dfg_async en background y persiste
data_dir/dfg_scan_status.json consultable. Devuelve el estado al agente:
  - "start"     → lanza el scan en background
  - "status"    → consulta el estado actual (running | ok | failed | not_started)

Uso en el agente:
    walker <project>/task/cpg_dfg_scan '{"action":"start"}'
    walker <project>/task/cpg_dfg_scan '{"action":"status"}'
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from libs.shared_domain.validated_vo import TaskInput
from features.code_analysis import CPGBuilder
from features.project import ProjectService


@dataclass(frozen=True)
class DfgScanInput(TaskInput):
    """Input validado de cpg_dfg_scan.

    action: "start" (lanza scan en background) | "status" (consulta estado).
    """

    action: str = "start"

    def _validate(self) -> str:
        if self.action not in ("start", "status"):
            return "action debe ser 'start' o 'status'"
        return ""


def cpg_dfg_scan(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")
    try:
        paths = ProjectService.resolve_paths(chain_dir)
        builder = CPGBuilder(str(paths.project_root))
    except Exception as e:
        return TaskResult.failure(f"Error resolviendo proyecto: {e}")

    input_result = DfgScanInput.from_dict(data)
    if not input_result.ok:
        return TaskResult.failure(input_result.error or "DfgScanInput inválido")
    inp: DfgScanInput = input_result.data
    action = inp.action

    if action == "status":
        status = builder.dfg_scan_status()
        status["action"] = "status"
        return TaskResult.success(data=status)

    # default: start (background, no bloquea)
    result = builder.scan_dfg_async([str(paths.project_root)])
    return TaskResult.success(data={
        "action": "start",
        "message": "DFG security scan lanzado en background",
        "status": result.get("status"),
        "status_path": result.get("status_path"),
        "next_step": "walker <project>/task/cpg_dfg_scan '{\"action\":\"status\"}' "
                     "para consultar el resultado",
    })


def register(registry: Any) -> None:
    registry.register("cpg_dfg_scan", cpg_dfg_scan,
                      metadata={"description": "DFG security scan asíncrono (start/status)",
                                "tags": ["cpg", "security", "async"]})
