"""use_cases/index_repo.py — IndexRepoUseCase.

Indexa un repo externo como referencia en el CKG.

Escribe via CodeKnowledgeGraph (formato canonico del indice 'ckg' de
CognitiveGraph), igual que save_to_ckg/ckg_analyze. SSOT: NUNCA usar el
KnowledgeGraph legacy (v3.0) que escribe en el mismo ckg.json.gz con
otro formato y corrompe el indice.
"""
from __future__ import annotations

import os

from returns.result import Result, safe

from features.project import ProjectService


class IndexRepoUseCase:
    """Index an external repo as reference in CKG."""

    @staticmethod
    def execute(
        chain_dir: str, url: str, name: str, branch: str = "main"
    ) -> Result[dict, Exception]:
        import shutil
        import subprocess
        import tempfile
        from features.code_analysis import run_outline, parse_outline
        from features.cognitive_knowledge import ckg_service

        @safe
        def _index() -> dict:
            paths = ProjectService.resolve_paths(chain_dir)
            tmpdir = tempfile.mkdtemp(prefix=f"kg-ref-{name}-")
            repo_path = os.path.join(tmpdir, name)
            try:
                print(f"  Cloning {url}...")
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

                print("  Scanning structure...")
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

                # Nodos en formato canonico (label obligatorio en
                # CognitiveCkgRepository.save_nodes). El resto de campos van
                # como metadata/tags del nodo del indice 'ckg'.
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
