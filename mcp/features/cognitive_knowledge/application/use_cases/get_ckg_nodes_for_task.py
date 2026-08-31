"""use_cases/get_ckg_nodes_for_task.py — GetCkgNodesForTaskUseCase.

Obtiene nodos CKG relevantes a los archivos de una tarea.
SSOT: This is the ONLY way engine/presenter should access CKG data.
"""
from __future__ import annotations

import json

from returns.pipeline import is_successful
from returns.result import Result, Success, safe

from ._common import _get_ckg


class GetCkgNodesForTaskUseCase:
    """Get CKG nodes relevant to a task's files."""

    @staticmethod
    def execute(
        chain_dir: str, task_id: str, task_files: list[str]
    ) -> Result[list[dict], Exception]:
        if not task_files:
            return Success([])

        @safe
        def _query() -> list[dict]:
            ckg = _get_ckg(chain_dir)
            raw = ckg.query()
            relevant: list[dict] = []
            for doc in raw:
                doc_text = json.dumps(doc).lower()
                for tf in task_files:
                    tf_lower = tf.lower()
                    if tf_lower in doc_text:
                        relevant.append(doc)
                        break
                    contains = doc.get("contains", [])
                    if isinstance(contains, list) and any(
                        tf_lower in c.lower() for c in contains
                    ):
                        relevant.append(doc)
                        break
                    node_file = doc.get("file", "")
                    if isinstance(node_file, str) and tf_lower in node_file.lower():
                        relevant.append(doc)
                        break
            return relevant[-5:]

        result = _query()
        if is_successful(result):
            return result
        else:
            return Success([])
