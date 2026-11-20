"""fast_mcp.shared_kernel.telemetry — medición de lo que el agente paga.

F0 de docs/AGENT-EFFICIENCY.md §2.7: sin latencia, tokens de salida y
`outcome` por tool no se puede decidir qué superficie merece existir
(AE-INV-05/AE-INV-08).

El registro es append-only JSONL (stdlib, sin dependencias): una línea por
llamada. El reporte vive en `scripts/agent_metrics.py`.
"""

from fast_mcp.shared_kernel.telemetry.tool_metrics import (
    CallRecord,
    classify_outcome,
    install_tool_metrics,
    metrics_path,
    read_records,
    record_call,
    record_exception,
    summarize,
)

__all__ = [
    "CallRecord",
    "classify_outcome",
    "install_tool_metrics",
    "metrics_path",
    "read_records",
    "record_call",
    "record_exception",
    "summarize",
]
