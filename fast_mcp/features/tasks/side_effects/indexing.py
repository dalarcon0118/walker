"""side_effects/indexing.py — efectos de índice de código.

`index_code`, `build_project_graph` y `sync_delta` estaban declarados por el
grafo (ANALYZE/DESIGN) y no existían: el motor los reportaba como
"unknown side effect". Son `optional: true` en el grafo, así que su fallo no
rompe el paso, pero el CPG nunca se actualizaba desde el workflow.

Delegan en las tools de memoria (`update_incremental_code` / `build_code_index`)
a través del composition root: este paquete no importa features.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _memory(name: str):
    from fast_mcp.composition import memory_tool_runner

    return memory_tool_runner(name)


def _incremental(chain_dir: str, task_id: str) -> dict[str, Any]:
    result = _memory("update_incremental_code")(chain_dir=chain_dir, task_id=task_id)
    if not isinstance(result, dict):
        return {"ok": False, "error": f"respuesta inesperada: {type(result).__name__}"}
    if result.get("error"):
        return {"ok": False, "error": str(result["error"])}
    updated = bool(result.get("updated"))
    # Un update abortado NO se reporta como exitoso (misma regla que el handler).
    return {"ok": updated, "indexed": updated, "result": result}


def index_code(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Actualización incremental del CPG con los archivos cambiados."""
    from ._context import task_id_of

    params = params or {}
    return _incremental(chain_dir, task_id_of(params, kwargs))


def build_project_graph(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Rebuild completo del CPG (scope=full)."""
    params = params or {}
    try:
        result = _memory("build_code_index")(chain_dir=chain_dir)
    except Exception as exc:  # noqa: BLE001 — efecto opcional: se reporta
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(result, dict):
        return {"ok": False, "error": f"respuesta inesperada: {type(result).__name__}"}
    if result.get("error"):
        return {"ok": False, "error": str(result["error"])}
    built = bool(result.get("built"))
    return {"ok": built, "built": built, "result": result}


def sync_delta(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Sincroniza el grafo de código con el estado actual.

    El delta de PLANES es el SSOT y no tiene índice aparte; lo que se
    sincroniza es el CPG (código). Antes este efecto hacía un `grep` por
    `task_id` y reportaba "actualizado" sin actualizar nada.
    """
    from ._context import task_id_of

    params = params or {}
    result = _incremental(chain_dir, task_id_of(params, kwargs))
    if result.get("ok"):
        result["synced"] = True
    return result


def register(registry) -> None:
    registry.register("index_code", index_code)
    registry.register("build_project_graph", build_project_graph)
    registry.register("sync_delta", sync_delta)
