"""use_cases/collect_knowledge.py — CollectKnowledgeUseCase.

Recolecta y persiste conocimiento desde la ejecución de una tarea.
"""
from __future__ import annotations

from returns.result import Result, safe

from ._common import _get_ckg


class CollectKnowledgeUseCase:
    """Collect and persist knowledge from task execution."""

    @staticmethod
    def execute(
        chain_dir: str, data: dict, task_id: str = ""
    ) -> Result[dict, Exception]:
        @safe
        def _collect() -> dict:
            ckg = _get_ckg(chain_dir)
            all_nodes = []
            for category in [
                "artefactos",
                "reglas",
                "decisiones",
                "advertencias",
                "fuera_scope",
            ]:
                items = data.get(category, [])
                for item in items:
                    if isinstance(item, str):
                        all_nodes.append(
                            {
                                "label": item,
                                "type": category.rstrip("s"),
                                "metadata": {"source": category},
                            }
                        )
                    elif isinstance(item, dict):
                        all_nodes.append(
                            {
                                "label": item.get("label", item.get("desc", str(item))),
                                "description": item.get("description", ""),
                                "type": category.rstrip("s"),
                                "metadata": {
                                    "source": category,
                                    **(item.get("metadata", {})),
                                },
                            }
                        )
            result = {"captured": True, "items": len(all_nodes)}
            if all_nodes:
                save_result = ckg.save_nodes(all_nodes)
                result["nodes_created"] = save_result.get("nodes_created", 0)
            return result

        return _collect()
