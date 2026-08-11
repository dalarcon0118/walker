"""
application/use_cases.py — Cognitive Knowledge domain use cases.

Refactorizado para usar Result pattern (dry-python/returns).
Cada use case retorna Result[T, Exception] en vez de dicts con ok/error.
"""

from __future__ import annotations

import gzip
import json
import os

from returns.result import Result, Success, safe
from returns.pipeline import is_successful

from features.project import ProjectService


# ── CKG health check (SSOT: solo cognitive_knowledge lee archivos CKG) ────


class CheckCkgHealthUseCase:
    """Check CKG health: node count and status.

    SRP: Single source of truth for CKG health data.
    """

    @staticmethod
    def execute(projects_data_dir: str) -> Result[dict, Exception]:
        """Check CKG status in the given data directory.

        Returns:
            Result[dict, Exception] con status y node_count
        """

        @safe
        def _read_ckg() -> dict:
            # Try .json.gz first (canonical), then .json (legacy)
            ckg_path = os.path.join(projects_data_dir, "ckg.json.gz")
            data = _read_ckg_file(ckg_path)
            if data is not None:
                total = len(data.get("nodes", data.get("docs", [])))
                return {"status": f"{total} nodes", "node_count": total}

            ckg_old = os.path.join(projects_data_dir, "ckg.json")
            old_data = _read_ckg_file(ckg_old)
            if old_data is not None:
                total = len(old_data.get("nodes", old_data.get("docs", [])))
                return {"status": f"{total} nodes", "node_count": total}

            return {"status": "empty", "node_count": 0}

        return _read_ckg()


def _read_ckg_file(path: str) -> dict | None:
    """Read a CKG file (.json or .json.gz). Returns None on error/missing."""
    if not os.path.exists(path):
        return None
    try:
        if path.endswith(".gz"):
            with gzip.open(path) as f:
                return json.load(f)
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _get_ckg(chain_dir: str):
    from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph

    paths = ProjectService.resolve_paths(chain_dir)
    return CodeKnowledgeGraph(str(paths.project_root))


class GatherCkgContextUseCase:
    """Gather CKG context for a task."""

    @staticmethod
    def execute(
        chain_dir: str,
        query: str = "type:convention OR type:decision",
        task_id: str = "",
    ) -> Result[list, Exception]:
        @safe
        def _gather() -> list:
            from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph

            paths = ProjectService.resolve_paths(chain_dir)
            ckg = CodeKnowledgeGraph(str(paths.project_root))
            q = f"task_id:{task_id}" if task_id else query
            return ckg.search(q)[:20]

        result = _gather()
        if is_successful(result):
            return result
        else:
            return Success([])


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


class GetCkgNodesForTaskUseCase:
    """Get CKG nodes relevant to a task's files.

    SSOT: This is the ONLY way engine/presenter should access CKG data.
    """

    @staticmethod
    def execute(
        chain_dir: str, task_id: str, task_files: list[str]
    ) -> Result[list[dict], Exception]:
        if not task_files:
            return Success([])

        @safe
        def _query() -> list[dict]:
            from features.cognitive_knowledge.application.ckg import (
                CodeKnowledgeGraph as CKG,
            )

            paths = ProjectService.resolve_paths(chain_dir)
            ckg = CKG(str(paths.project_root))
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


class IndexRepoUseCase:
    """Index an external repo as reference in CKG.

    Escribe via CodeKnowledgeGraph (formato canonico del indice 'ckg' de
    CognitiveGraph), igual que save_to_ckg/ckg_analyze. SSOT: NUNCA usar el
    KnowledgeGraph legacy (v3.0) que escribe en el mismo ckg.json.gz con
    otro formato y corrompe el indice.
    """

    @staticmethod
    def execute(
        chain_dir: str, url: str, name: str, branch: str = "main"
    ) -> Result[dict, Exception]:
        import shutil
        import subprocess
        import tempfile
        from features.code_analysis.infrastructure.ast_grep_adapter import (
            run_outline,
            parse_outline,
        )
        from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph

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
                ckg = CodeKnowledgeGraph(str(paths.project_root))
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


class ResearchTopicUseCase:
    """Research a topic on web and save to CKG.

    Escribe via CodeKnowledgeGraph (formato canonico del indice 'ckg' de
    CognitiveGraph), igual que save_to_ckg/ckg_analyze. SSOT: NUNCA usar el
    KnowledgeGraph legacy (v3.0) que escribe en el mismo ckg.json.gz con
    otro formato y corrompe el indice.
    """

    @staticmethod
    def execute(chain_dir: str, topic: str, query: str = "") -> Result[dict, Exception]:
        import urllib.request
        import urllib.parse
        from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph

        @safe
        def _research() -> dict:
            paths = ProjectService.resolve_paths(chain_dir)
            query_final = query or f"{topic} guide tutorial"
            try:
                search_url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query_final)}&format=json&no_html=1"
                try:
                    with urllib.request.urlopen(search_url, timeout=15) as resp:
                        web_data = json.loads(resp.read())
                    abstract = web_data.get("AbstractText", "")
                    source_url = web_data.get(
                        "AbstractURL", f"https://duckduckgo.com/?q={query_final}"
                    )
                except Exception:
                    abstract = f"Research on {topic}"
                    source_url = ""
                node = {
                    "label": (
                        f"{topic}: {abstract[:200] if abstract else 'research completed'}"
                    ),
                    "type": "component",
                    "subject": topic.lower(),
                    "query_hints": [topic.lower(), query_final.lower()],
                    "tags": ["external", topic.lower().replace(" ", "-")],
                    "metadata": {"source": "web", "url": source_url},
                    "author": "opencode",
                    "source": "web-research",
                }
                ckg = CodeKnowledgeGraph(str(paths.project_root))
                result = ckg.save_nodes([node])
                node_ids = result.get("node_ids", [])
                return {
                    "topic": topic,
                    "abstract": abstract[:200],
                    "source": source_url,
                    "node_id": node_ids[0] if node_ids else "",
                    "saved": bool(result.get("ok", False)),
                }
            except Exception:
                raise

        return _research()
