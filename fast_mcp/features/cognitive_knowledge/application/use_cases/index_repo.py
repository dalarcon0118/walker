"""use_cases/index_repo.py — IndexRepoUseCase.

Indexes an external repo as reference in the CKG.

Writes via CodeKnowledgeGraph (canonical format of the 'ckg' index of
CognitiveGraph), same as save_to_ckg/ckg_analyze. SSOT: NEVER use the
KnowledgeGraph legacy (v3.0) that writes to the same ckg.json.gz with
a different format and corrupts the index.
"""
from __future__ import annotations

import os

from returns.result import Result, safe

from fast_mcp.shared_kernel.project_support import ProjectService


class IndexRepoUseCase:
    """Index an external repo as reference in CKG."""

    @staticmethod
    def execute(
        chain_dir: str, url: str, name: str, branch: str = "main"
    ) -> Result[dict, Exception]:
        import shutil
        import subprocess
        import tempfile
        from fast_mcp.features.code_analysis.infrastructure.ast_grep_adapter import (
            run_outline,
            parse_outline,
        )
        from fast_mcp.features.cognitive_knowledge import ckg_service

        @safe
        def _index() -> dict:
            paths = ProjectService.resolve_paths(chain_dir)
            tmpdir = tempfile.mkdtemp(prefix=f"kg-ref-{name}-")
            repo_path = os.path.join(tmpdir, name)
            try:
                subprocess.run(
                    [
                        "git",
                        "clone",
                        "--depth",
                        "1",
                        "--branch",
                        branch,
                        url,
                        repo_path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if not os.path.isdir(repo_path):
                    raise RuntimeError(f"Failed to clone {url}")

                results = run_outline([repo_path])
                graph = parse_outline(results) if results else None
                if not graph:
                    return {
                        "ok": True,
                        "message": "No parseable files found",
                        "url": url,
                    }

                types = {}
                for n in graph.nodes.values():
                    types[n.type] = types.get(n.type, 0) + 1

                nodes_data = [
                    {
                        "label": f"Reference repo: {name} ({url})",
                        "type": "reference",
                        "subject": name,
                        "query_hints": [name.lower(), url.lower()],
                        "tags": ["reference", "external", name.lower()],
                        "metadata": {
                            "url": url,
                            "branch": branch,
                            "nodes_total": len(graph.nodes),
                        },
                    }
                ]
                type_labels = {
                    "function": "functions",
                    "class": "classes",
                    "module": "modules",
                    "interface": "interfaces",
                    "type": "types",
                }
                for t, count in sorted(types.items(), key=lambda x: -x[1])[:10]:
                    if t == "file":
                        continue
                    nodes_data.append(
                        {
                            "label": f"{name} has {count} {type_labels.get(t, t)}",
                            "type": "pattern",
                            "subject": f"{name}:{t}",
                            "query_hints": [name.lower(), t.lower()],
                            "tags": ["reference", name.lower(), t],
                            "metadata": {"url": url, "type": t, "count": count},
                        }
                    )
                ckg = ckg_service(str(paths.project_root))
                result = ckg.save_nodes(nodes_data)
                shutil.rmtree(tmpdir, ignore_errors=True)
                return {
                    "ok": result.get("ok", True),
                    "url": url,
                    "name": name,
                    "nodes_indexed": len(nodes_data),
                    "structure": {
                        t: c for t, c in sorted(types.items(), key=lambda x: -x[1])
                    },
                }
            except Exception:
                shutil.rmtree(tmpdir, ignore_errors=True)
                raise

        return _index()
