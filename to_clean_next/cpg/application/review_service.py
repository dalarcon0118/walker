"""application/review_service.py — Code review use case.

Usa qlty para métricas numéricas (cyclo, LOC, smells, lint).
Usa CPG para análisis estructural (dead code, call graph).
Todo en batch, no por archivo.
"""
from __future__ import annotations
import os
from typing import Any

from cpg.domain.graph import Graph
from cpg.domain.metrics import ReviewResult, ProjectMetrics
from cpg.infrastructure.repository import CpgFileRepository
from cpg.infrastructure.metrics import (
    run_metrics, run_function_metrics, run_smells, run_check,
    compute_file_metrics, check_quality_gates,
)


class ReviewUseCase:
    """Code review: metrics + quality gates + smells + lint."""

    def __init__(self, chain_dir: str, project_id: str = "",
                 scan_paths: list[str] | None = None):
        self.chain_dir = chain_dir
        self._project_id = project_id
        self._scan_paths = scan_paths
        self._graph: Graph | None = None
        self._repo: CpgFileRepository | None = None

    @property
    def repo(self) -> CpgFileRepository:
        if self._repo is None:
            from _walker.shared.path_resolver import resolve_project_root, get_data_dir
            project_root = resolve_project_root(self.chain_dir)
            project_id = os.path.basename(project_root) if project_root else ""
            data_dir = str(get_data_dir(project_id)) if project_id else ""
            self._repo = CpgFileRepository(data_dir or os.path.join(project_root, "data") if project_root else "")
        return self._repo

    @property
    def graph(self) -> Graph:
        if self._graph is None:
            self._graph = self.repo.load_graph(self._project_id)
        return self._graph

    def review_files(self, files: list[str] | None = None,
                     thresholds: dict[str, Any] | None = None) -> ReviewResult:
        """Code review: metrics + quality gates + smells + lint.

        Todo en batch via qlty + CPG.
        """
        graph = self.graph
        scan_paths = files or self._scan_paths or self._resolve_default_paths()

        # 1. File metrics (batch)
        qlty_metrics = run_metrics(scan_paths)
        qlty_funcs = run_function_metrics(scan_paths)

        # 2. Build per-file metrics
        per_file = []
        high_cyclo_funcs = []
        cyclo_threshold = (thresholds or {}).get("max_cyclo", 10)

        for qf in qlty_metrics:
            fp = qf.get("file", "")
            # Normalizar path: qlty usa paths relativos, CPG usa absolutos
            abs_fp = os.path.abspath(fp) if not os.path.isabs(fp) else fp
            funcs = qlty_funcs.get(fp, qlty_funcs.get(abs_fp, []))
            fm = compute_file_metrics(fp, qf, funcs, graph)
            per_file.append(fm)

            for fn in funcs:
                cyclo = fn.get("cyclo", 0)
                if cyclo > cyclo_threshold:
                    # Enriquecer con contexto del CPG
                    callers = []
                    callees = []
                    for nid, node in graph.nodes.items():
                        if node.name == fn["function"] and node.path == fp:
                            for oe in graph.get_outgoing(nid):
                                if oe["type"] == "CALLS":
                                    tgt = graph.get_node(oe["to"])
                                    if tgt:
                                        callees.append(tgt.name)
                            for ie in graph.get_incoming(nid):
                                if ie["type"] == "CALLS":
                                    src = graph.get_node(ie["from"])
                                    if src:
                                        callers.append(src.name)
                            break

                    high_cyclo_funcs.append({
                        "function": fn["function"],
                        "file": fp,
                        "line": fn.get("line", 0),
                        "cyclo": cyclo,
                        "cognitive": fn.get("cognitive", 0),
                        "lines": fn.get("lines", 0),
                        "loc": fn.get("loc", 0),
                        "callers": callers[:10],
                        "callees": callees[:15],
                    })

        # 3. Dead code from CPG
        dead_code_list = []
        _main_guard_cache: dict[str, bool] = {}

        def _has_main_guard(filepath: str) -> bool:
            """Check if file has if __name__ == '__main__' guard (CLI entry point)."""
            if filepath in _main_guard_cache:
                return _main_guard_cache[filepath]
            try:
                with open(filepath) as f:
                    content = f.read()
                has_guard = (
                    '__name__ == "__main__"' in content
                    or "__name__ == '__main__'" in content
                    or 'if __name__ == "__main__"' in content
                    or "if __name__ == '__main__'" in content
                )
                _main_guard_cache[filepath] = has_guard
                return has_guard
            except (OSError, IOError):
                _main_guard_cache[filepath] = False
                return False

        for nid, node in graph.nodes.items():
            if node.type not in ("function", "method"):
                continue
            incoming = [ie for ie in graph.get_incoming(nid) if ie["type"] == "CALLS"]
            if incoming or node.name.startswith("__"):
                continue
            # Skip functions named 'main' in files with __name__ guard (CLI entry points)
            if node.name == "main" and _has_main_guard(node.path):
                continue
            dead_code_list.append({
                "function": node.name,
                "file": node.path,
                "line": node.line,
                "is_private": node.name.startswith("_"),
            })

        # 4. Smells + lint (batch SARIF)
        smells = run_smells(scan_paths)
        lint_issues = run_check(scan_paths)

        # 5. Aggregate metrics
        totals = {
            "files": len(per_file),
            "loc": sum(f.loc for f in per_file),
            "functions": sum(f.functions for f in per_file),
            "classes": sum(f.classes for f in per_file),
            "cyclo_max": max(f.cyclo_max for f in per_file) if per_file else 0,
            "dead_code": len(dead_code_list),
        }

        metrics = ProjectMetrics(
            total_files=totals["files"],
            total_loc=totals["loc"],
            total_functions=totals["functions"],
            total_classes=totals["classes"],
            cyclo_max=totals["cyclo_max"],
            dead_code_count=totals["dead_code"],
            files=per_file[:200],
        )

        # 6. Quality gates
        gates = check_quality_gates(metrics, thresholds)
        passed = all(g.passed for g in gates)

        # 7. Findings
        findings = {}
        if high_cyclo_funcs:
            findings["high_complexity"] = sorted(
                high_cyclo_funcs, key=lambda x: -x["cyclo"]
            )[:20]
        if dead_code_list:
            findings["dead_code"] = dead_code_list[:30]

        # 8. Summary + Recommendations
        parts = [f"{'✅ PASS' if passed else '❌ FAIL'}"]
        for g in gates:
            if not g.passed:
                parts.append(f"{g.name}={g.actual} (max {g.threshold})")
        if smells:
            parts.append(f"smells={len(smells)}")
        if lint_issues:
            parts.append(f"lint={len(lint_issues)}")

        # Generate actionable recommendations
        recommendations = self._build_recommendations(
            metrics, gates, findings, smells, lint_issues, thresholds or {}
        )

        return ReviewResult(
            passed=passed,
            metrics=metrics,
            gates=gates,
            findings=findings,
            recommendations=recommendations,
            issues=(smells + lint_issues)[:50],
            files_scanned=[f.file for f in per_file[:50]],
            summary=", ".join(parts),
        )

    def _build_recommendations(self, metrics, gates, findings, smells, lint_issues, thresholds):
        """Generate actionable recommendations based on metrics + findings."""
        recs = []
        max_cyclo = thresholds.get("max_cyclo", 10)
        max_dead = thresholds.get("max_dead_code", 50)

        # Priority 1: High complexity
        hc = findings.get("high_complexity", [])
        if hc:
            top = hc[0]
            # Generar estrategias de refactor según el tipo de función
            strategies = []
            if top["cyclo"] > 50:
                strategies.append("Extraer bloques condicionales anidados a funciones helper")
            if top.get("callees", []):
                strategies.append(f"Divide en {len(top['callees'])} llamadas más pequeñas y modulares")
            if top.get("callers"):
                strategies.append("Revisar si acepta太多 parámetros o responsabilidades")
            strategies.append("Reemplazar cadenas if-elif con dict dispatch o pattern matching")
            strategies.append("Usar guard clauses para reducir anidamiento")

            # Top 5 archivos por complejidad
            files_by_cyclo = {}
            for f in hc:
                fp = f["file"]
                files_by_cyclo.setdefault(fp, []).append(f["cyclo"])
            top_files = sorted(files_by_cyclo.items(), key=lambda x: -max(x[1]))[:5]

            file_context = []
            for fp, cyclos in top_files:
                total = sum(cyclos)
                funcs_in_file = len(cyclos)
                file_context.append({
                    "file": fp.split("/projects/delta-skill/")[-1] if "/projects/delta-skill/" in fp else fp,
                    "high_cyclo_funcs": funcs_in_file,
                    "total_cyclo": total,
                })

            recs.append({
                "priority": "P1 - Critical",
                "area": "Complexity",
                "action": f"Refactor high-complexity functions. Top target: {top['function']} (cyclo={top['cyclo']}, {top.get('lines',0)} lines). "
                         f"Called by {len(top.get('callers',[]))} functions, calls {len(top.get('callees',[]))} functions.",
                "impact": f"Reducing cyclo from {metrics.cyclo_max} to <{max_cyclo} would pass the gate.",
                "strategies": strategies,
                "files_most_affected": file_context,
                "targets": [{
                    "function": f["function"],
                    "cyclo": f["cyclo"],
                    "cognitive": f.get("cognitive", 0),
                    "lines": f.get("lines", 0),
                    "file": f["file"].split("/projects/delta-skill/")[-1] if "/projects/delta-skill/" in f["file"] else f["file"],
                    "callers": f.get("callers", [])[:5],
                    "callees": f.get("callees", [])[:8],
                } for f in hc[:5]],
            })

        # Priority 2: Dead code
        dc = findings.get("dead_code", [])
        if dc:
            priv = [f for f in dc if f.get("is_private")]
            pub = [f for f in dc if not f.get("is_private")]
            recs.append({
                "priority": "P2 - High",
                "area": "Dead Code",
                "action": f"Remove {len(priv)} private unused functions (safe). "
                         f"Review {len(pub)} public unused functions: integrate or delete.",
                "impact": f"Reducing from {len(dc)} to <{max_dead} would pass the gate.",
                "targets": [{"function": f["function"], "private": f.get("is_private"),
                             "file": f["file"].split("/projects/delta-skill/")[-1],
                             "line": f["line"]} for f in dc[:10]],
            })

        # Priority 3: Smells
        if smells:
            by_type = {}
            for s in smells:
                tid = s["id"].split(":")[-1] if ":" in s["id"] else s["id"]
                by_type[tid] = by_type.get(tid, 0) + 1
            top_smells = sorted(by_type.items(), key=lambda x: -x[1])[:5]
            recs.append({
                "priority": "P3 - Medium",
                "area": "Code Smells",
                "action": f"Fix {len(smells)} code smells. Top types: " +
                         ", ".join(f"{t}({c})" for t, c in top_smells),
                "impact": "Reduces technical debt, improves maintainability.",
                "targets": [{"type": t, "count": c} for t, c in top_smells],
            })

        # Priority 4: Lint
        if lint_issues:
            by_linter = {}
            for l in lint_issues:
                lid = l["id"].split(":")[0] if ":" in l["id"] else "other"
                by_linter[lid] = by_linter.get(lid, 0) + 1
            top_linters = sorted(by_linter.items(), key=lambda x: -x[1])[:5]
            recs.append({
                "priority": "P4 - Low",
                "area": "Linting",
                "action": f"Fix {len(lint_issues)} linter issues. Auto-fix with: qlty check --fix",
                "impact": "Enforces code style, catches bugs early.",
                "targets": [{"linter": t, "count": c} for t, c in top_linters],
            })

        return recs

    def _resolve_default_paths(self) -> list[str]:
        from _walker.shared.path_resolver import resolve_project_root, resolve_source_code_dirs
        proj_dir = resolve_project_root(self.chain_dir)
        project_id = os.path.basename(proj_dir) if proj_dir else ""
        if project_id:
            dirs = resolve_source_code_dirs(project_id)
            return [str(d) for d in dirs]
        return [self.chain_dir]
