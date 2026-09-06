"""fast_mcp/features/tasks/handlers/_inspect_codebase_helper.py

Helper para ejecutar actions de inspect_codebase vía ICpgAnalysisPort
(adapter inyectado en ExecutionContext por el composition root).

Si `data["inspect_codebase_action"]` está presente y el adapter está
disponible, delega en el método correspondiente y retorna el resultado.
En cualquier otro caso es un noop (retorna None).
"""

from __future__ import annotations

import inspect
from typing import Any


def run_inspect_codebase(adapter: Any, data: dict[str, Any]) -> dict[str, Any] | None:
    """Ejecuta una action de inspect_codebase vía el adapter CPG.

    Solo inyecta los parámetros que el método del adapter acepta (fail-fast
    ante parámetros desconocidos, nunca los ignora silenciosamente).
    """
    action = data.get("inspect_codebase_action")
    if not action or adapter is None:
        return None

    method = getattr(adapter, action, None)
    if method is None:
        return {"error": f"Unknown inspect_codebase action: {action}"}

    candidate_params = {
        key: data[key]
        for key in (
            "file",
            "files",
            "symbol",
            "query",
            "query_text",
            "scope",
            "bug_type",
            "vuln_type",
            "focus",
            "file_filter",
            "depth",
            "action",
            "project_root",
        )
        if key in data
    }
    try:
        sig = inspect.signature(method)
        params = {
            k: v for k, v in candidate_params.items()
            if k in sig.parameters
        }
    except (TypeError, ValueError):
        params = candidate_params

    try:
        return method(**params)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
