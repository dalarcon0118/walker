"""code_analysis/infrastructure/adapters/walker_cpg_adapter.py — Adapter hacia task_execution.

Implementa ICpgAnalysisPort (puerto definido en task_execution) orquestando
CognitiveGraph + servicios de dominio de code_analysis.

SRP: este archivo es el ÚNICO punto de acoplamiento entre code_analysis
y task_execution. Si task_execution cambia el puerto, solo este adapter
se actualiza.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Any

from features.code_analysis.application.ports.cpg_repository import CpgRepository
from features.code_analysis.domain.events import AnalysisCompleted, EventBus
from features.code_analysis.domain.services.analysis_service import IBusinessAnalyzer
from features.code_analysis.domain.services.business_analyzer import BusinessAnalyzer, analyze
from features.task_execution.ports.cpg_analysis_port import ICpgAnalysisPort


class WalkerCpgAnalysisAdapter(ICpgAnalysisPort):
    """Implementación del puerto de task_execution sobre code_analysis."""

    def __init__(self, repo: CpgRepository, analyzer: IBusinessAnalyzer | None = None, event_bus: EventBus | None = None):
        self._repo = repo
        self._analyzer = analyzer
        self._bus = event_bus

    def business_layer(self, focus: str = "") -> dict[str, object]:
        data = self._repo.load()
        nodes = data.get("nodes", {})
        edges = data.get("edges", [])

        if focus:
            nodes = self._filter_nodes(nodes, focus)

        if self._analyzer is not None:
            result = self._analyzer.analyze({"file": focus} if focus else None)
        else:
            result = analyze(nodes, edges)

        self._emit(AnalysisCompleted(
            analysis_type="business_layer",
            scope=focus or "project",
            result_summary={
                "pure_functions": len(result.get("pure_functions", [])),
                "flows": len(result.get("business_flows", [])),
                "patterns": len(result.get("architecture_patterns", [])),
            },
        ))
        return result

    def detect_unused_functions(self, file_filter: str = "") -> dict[str, object]:
        query: dict = {"type": "function"}
        if file_filter:
            query["file"] = file_filter
        result = self._repo.search_nodes(query, max_results=200)
        hits = result.get("hits", {}).get("hits", [])
        candidates = []
        for h in hits:
            src = h.get("_source", {})
            if src.get("is_import"):
                continue
            candidates.append({
                "id": h.get("_id", ""),
                "name": src.get("name", ""),
                "file": src.get("path", src.get("file", "")),
                "line": src.get("line", 0),
                "type": src.get("type", ""),
            })
        result = {
            "query": "bug:unused_functions",
            "total": len(candidates),
            "matches": candidates,
        }
        self._emit(AnalysisCompleted(
            analysis_type="dead_code",
            scope=file_filter or "project",
            result_summary={"total": len(candidates)},
        ))
        return result

    def gather_context(self, files: list[str] | None = None) -> dict[str, object]:
        if files:
            context = {}
            for f in files:
                result = self._repo.search_nodes({"file": f})
                if result.get("hits", {}).get("total"):
                    context[f] = result
            return {"files": context, "files_analyzed": len(files)}

        result = self._repo.search_nodes({"type": "function"}, max_results=20)
        hits = result.get("hits", {}).get("hits", [])
        if not hits:
            result = self._repo.search_nodes({"type": "class"}, max_results=20)
        return {
            "structure": result,
            "files_analyzed": 0,
        }

    def review_code(self, files: list[str] | None = None, thresholds: dict | None = None) -> dict[str, object]:
        from features.code_analysis.application.review_service import ReviewUseCase
        from features.project import ProjectService

        data_dir = ""
        try:
            paths = ProjectService.resolve_paths(self._repo._file_repo.data_dir)
            data_dir = paths.data_dir or ""
        except Exception:
            data_dir = self._repo._file_repo.data_dir

        svc = ReviewUseCase(self._repo._file_repo.data_dir, data_dir=data_dir or None)
        result = svc.review_files(files=files, thresholds=thresholds)
        result_dict = result.to_dict()
        self._emit(AnalysisCompleted(
            analysis_type="code_review",
            scope=",".join(files) if files else "project",
            result_summary={
                "passed": result_dict.get("passed"),
                "files": len(result_dict.get("files_scanned", [])),
                "gates": len(result_dict.get("gates", [])),
            },
        ))
        return result_dict

    def project_metrics(self, file_filter: str = "") -> dict[str, object]:
        from features.code_analysis.application.review_service import ReviewUseCase
        from features.project import ProjectService

        data_dir = ""
        try:
            paths = ProjectService.resolve_paths(self._repo._file_repo.data_dir)
            data_dir = paths.data_dir or ""
        except Exception:
            data_dir = self._repo._file_repo.data_dir

        svc = ReviewUseCase(self._repo._file_repo.data_dir, data_dir=data_dir or None)
        files = [file_filter] if file_filter else None
        result = svc.review_files(files=files)
        result_dict = result.to_dict()
        self._emit(AnalysisCompleted(
            analysis_type="project_metrics",
            scope=file_filter or "project",
            result_summary=result_dict.get("metrics", {}),
        ))
        return result_dict

    def scan_dfg(self, action: str = "start", project_root: str = "") -> dict[str, object]:
        from features.code_analysis import CPGBuilder

        builder = CPGBuilder(project_root)
        if action == "status":
            status = builder.dfg_scan_status()
            status["action"] = "status"
            return status

        result = builder.scan_dfg_async([project_root])
        self._emit(AnalysisCompleted(
            analysis_type="dfg_scan",
            scope=project_root,
            result_summary={"status": result.get("status")},
        ))
        return {
            "action": "start",
            "status": result.get("status"),
            "status_path": result.get("status_path"),
            "next_step": "Re-ejecuta con action='status' para consultar el resultado",
        }

    def build_cpg(self, project_root: str, scan_paths: list[str] | None = None) -> dict[str, object]:
        from features.code_analysis import CPGBuilder

        builder = CPGBuilder(project_root)
        result = builder.build(scan_paths)
        stats = result.get("stats", result)
        result_dict = {
            "built": True,
            "nodes": stats.get("total_nodes", len(result.get("nodes", {}))),
            "edges": stats.get("total_edges", len(result.get("edges", []))),
        }
        self._emit(AnalysisCompleted(
            analysis_type="cpg_build",
            scope=project_root,
            result_summary=result_dict,
        ))
        return result_dict

    def index_ref(self, url: str, name: str, branch: str = "main", depth: int = 1, data_dir: str = "") -> dict[str, object]:
        from features.code_analysis.infrastructure.ast_grep_adapter import run_outline, parse_outline

        out_path = os.path.join(data_dir, f"cpg.ref.{name}.json.gz")
        tmpdir = None
        try:
            tmpdir = tempfile.mkdtemp(prefix=f"cpg-ref-{name}-")
            repo_path = os.path.join(tmpdir, name)

            branches_to_try = [branch] if branch != "main" else ["main", "master"]
            cloned = False
            for b in branches_to_try:
                subprocess.run(
                    ["git", "clone", "--depth", str(depth), "--branch", b, "--single-branch", url, repo_path],
                    capture_output=True, text=True, timeout=120,
                )
                if os.path.isdir(repo_path):
                    branch = b
                    cloned = True
                    break
            if not cloned:
                return {"ok": False, "error": f"Failed to clone {url}"}

            results = run_outline([repo_path])
            if not results:
                return {"message": "No se encontraron archivos parseables", "url": url}

            graph = parse_outline(results)
            data_out = graph.to_dict()
            stats = {
                "version": "2.0",
                "project_id": f"ref:{name}",
                "generated_by": f"cpg_index_ref/{url}",
                "source_url": url,
                "source_branch": branch,
                "total_nodes": len(graph.nodes),
                "total_edges": len(graph.edges),
            }
            data_out.update(stats)

            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            payload = json.dumps(data_out, ensure_ascii=False, separators=(",", ":"))
            with open(out_path, "wb") as f:
                f.write(gzip.compress(payload.encode()))

            types = {}
            for n in graph.nodes.values():
                types[n.type] = types.get(n.type, 0) + 1

            result = {
                "url": url, "name": name, "index": f"cpg-ref-{name}",
                "path": out_path, "nodes": len(graph.nodes),
                "edges": len(graph.edges), "structure": types,
            }
            self._emit(AnalysisCompleted(
                analysis_type="cpg_index_ref",
                scope=name,
                result_summary=result,
            ))
            return result
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"Timeout clonando {url}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            if tmpdir:
                shutil.rmtree(tmpdir, ignore_errors=True)

    def _emit(self, event: AnalysisCompleted) -> None:
        if self._bus is not None:
            try:
                self._bus.publish(event)
            except Exception:
                pass

    @staticmethod
    def _filter_nodes(nodes: dict[str, dict], focus: str) -> dict[str, dict]:
        focus_lower = focus.lower()
        return {
            nid: node
            for nid, node in nodes.items()
            if isinstance(node, dict) and (
                focus_lower in node.get("path", "").lower()
                or focus_lower in node.get("name", "").lower()
            )
        }
