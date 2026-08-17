"""use_cases/save_to_ckg.py — SaveToCkgUseCase.

Guarda nodos de conocimiento en el CKG.
"""
from __future__ import annotations

from returns.result import Result, safe

from ._common import _get_ckg


class SaveToCkgUseCase:
    """Save knowledge nodes to CKG."""

    @staticmethod
    def execute(
        chain_dir: str, nodes: list[dict], task_id: str = "", session_id: str = ""
    ) -> Result[dict, Exception]:
        @safe
        def _save() -> dict:
            ckg = _get_ckg(chain_dir)
            if not nodes:
                return {"message": "no data to save", "saved": False}
            for item in nodes:
                if isinstance(item, dict) and "label" in item:
                    item.setdefault("metadata", {})
                    item["metadata"].setdefault("task_id", task_id)
                    item["metadata"].setdefault("source", f"session:{session_id}")
            result = ckg.save_nodes(nodes)
            return {
                "ok": True,
                "nodes_saved": result.get("nodes_created", 0),
                "result": result,
            }

        return _save()
