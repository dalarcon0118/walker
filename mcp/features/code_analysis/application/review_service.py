"""application/review_service.py — Code review use case.

Métricas CPG-nativas (sin binario externo qlty): todo se deriva del MISMO
build del grafo (compute_cpg_metrics en metrics_service) + dead code del
CPG + deep analysis ast-grep. Los wrappers qlty fueron eliminados
(ADR-WALKER-054, limpieza de dependencia tercerizada).

Return pattern: las operaciones fallibles dejan de tragarse errores con
`except: pass`; los fallos se loggean (convention-001: nunca silencioso).
"""

from __future__ import annotations
import logging
import os
from typing import Any

from returns.pipeline import is_successful

logger = logging.getLogger(__name__)

from features.code_analysis.domain.graph import Graph
from features.code_analysis.domain.metrics import ReviewResult, ProjectMetrics, QualityGate
from features.code_analysis.infrastructure.cognitive_cpg_repo import CognitiveCpgRepository


class ReviewUseCase:
    """Code review: metrics CPG-nativas + quality gates + dead code + deep ast-grep."""

    def __init__(
        self,
        chain_dir: str,
        project_id: str = "",
        scan_paths: list[str] | None = None,
        data_dir: str | None = None,
        cache: Any = None,  # legacy: caché qlty eliminada; se conserva el parámetro por compatibilidad de firma
    ):
        self.chain_dir = chain_dir
        self._project_id = project_id
        self._scan_paths = scan_paths
        self._data_dir = data_dir
        self._graph: Graph | None = None
        self._repo: CognitiveCpgRepository | None = None

    @property
    def repo(self) -> CognitiveCpgRepository:
        if self._repo is None:
            if self._data_dir:
                self._repo = CognitiveCpgRepository(self._data_dir, self._project_id)
            else:
                from app.path_resolver import resolve_project_root, get_data_dir

                project_root = resolve_project_root(self.chain_dir)
                project_id = os.path.basename(project_root) if project_root else ""
                data_dir = str(get_data_dir(project_id)) if project_id else ""
                self._project_id = self._project_id or project_id
                self._repo = CognitiveCpgRepository(
                    data_dir
                    or (os.path.join(project_root, "data") if project_root else ""),
                    self._project_id,
                )
        return self._repo

    @property
    def graph(self) -> Graph:
        if self._graph is None:
            self._graph = Graph.from_dict(self.repo.load(self._project_id))
        return self._graph

    def _resolve_repo_and_data_dir(self) -> tuple[str, str]:
        """Resuelve (repo_root, data_dir) para metrics_service (CPG-native).

        R1 (ADR-WALKER-055): delega en el ContextResolver compartido.
        repo_root: raíz real del código; data_dir: dir canónico de memorias.
        """
        data_dir = self._data_dir or self.repo.data_dir
        from app.path_resolver import resolve_memory_context

        ctx = resolve_memory_context(data_dir)
        return ctx.repo_root or ctx.data_dir, ctx.data_dir or data_dir

    def _scan_dead_code(
        self,
        graph: Graph,
        scanner: DeadCodeReferenceScanner,
        is_false_positive_dead_code,
    ) -> list[dict]:
        """Detecta dead code real desde el CPG (scanner ÚNICO, ADR-055 F7)."""
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
            except (OSError, IOError) as e:
                logger.debug(
                    "No se pudo leer %s para main-guard check (%s); se asume sin guard",
                    filepath, e,
                )
                _main_guard_cache[filepath] = False
                return False

        from features.code_analysis.domain.services.analysis_service import (
            find_unused_functions,
        )

        nodes = {
            nid: {
                "name": node.name,
                "type": node.type,
                "path": node.path,
                "line": node.line,
                "signature": getattr(node, "signature", "") or "",
                "is_import": bool(getattr(node, "is_import", False)),
            }
            for nid, node in graph.nodes.items()
        }
        edges = [e.to_dict() for e in graph.edges]
        found = find_unused_functions(
            nodes, edges,
            is_false_positive_dead_code=is_false_positive_dead_code,
            is_referenced=scanner.is_referenced,
            main_guard=lambda name, path: name == "main" and _has_main_guard(path),
        )
        return [
            {"function": f["name"], "file": f["file"], "line": f["line"],
             "is_private": f["is_private"]}
            for f in found
        ]

    def _run_deep_analysis(self, scan_paths: list[str]) -> list[dict]:
        """Deep analysis ast-grep (patrones de producción/seguridad) sin qlty.

        Corre sobre los archivos del scope escaneado; nunca lanza
        (best-effort con log, convention-001).
        """
        findings: list[dict] = []
        try:
            from features.code_analysis.infrastructure.codereview.deep import (
                deep_analyze_file,
            )
            from pathlib import Path

            for p in scan_paths:
                if not os.path.isfile(p):
                    continue
                deep = deep_analyze_file(Path(p))
                for f in deep.get("deep_findings", [])[:5]:
                    findings.append(f)
        except Exception as exc:
            logger.warning(
                "deep analysis fallo (%s: %s); findings omitidos",
                type(exc).__name__, exc,
            )
        return findings

    def review_files(
        self, files: list[str] | None = None, thresholds: dict[str, Any] | None = None
    ) -> ReviewResult:
        """Code review CPG-native: métricas + gates + dead code + deep ast-grep."""
        graph = self.graph
        thresholds = thresholds or {}
        repo_path, data_dir = self._resolve_repo_and_data_dir()
        scan_paths = files or self._scan_paths or self._resolve_default_paths()

        # 1. Métricas CPG-nativas (sin qlty)
        from features.code_analysis.application.metrics_service import (
            compute_cpg_metrics,
        )

        outcome = compute_cpg_metrics(
            repo_path=repo_path, data_dir=data_dir, scope=""
        )
        warnings: list[str] = []
        if is_successful(outcome):
            report = outcome.value_or(None)
            metrics = report.metrics if report else ProjectMetrics()
            warnings = list(report.warnings) if report else []
        else:
            err = str(outcome.failure())
            warnings = [err]
            metrics = ProjectMetrics()
            logger.error("review_files: métricas CPG-nativas fallaron (%s)", err)

        # 2. Dead code from CPG + filtro de falsos positivos
        from features.code_analysis.domain.analysis import (
            DeadCodeReferenceScanner,
            is_false_positive_dead_code,
        )

        scanner = DeadCodeReferenceScanner(
            sorted({n.path for n in graph.nodes.values() if n.path})
        )
        dead_code_list = self._scan_dead_code(
            graph, scanner, is_false_positive_dead_code
        )

        # 3. Deep analysis ast-grep (seguridad/producción)
        deep_findings = self._run_deep_analysis(scan_paths)

        # 4. Quality gates (cyclo no disponible en modo CPG-native)
        gates = [
            QualityGate(
                name="dead_code",
                actual=len(dead_code_list),
                threshold=thresholds.get("max_dead_code", 50),
                passed=len(dead_code_list) <= thresholds.get("max_dead_code", 50),
            ),
            QualityGate(
                name="loc_per_file",
                actual=metrics.total_loc // max(metrics.total_files, 1),
                threshold=thresholds.get("max_loc_per_file", 500),
                passed=(metrics.total_loc // max(metrics.total_files, 1))
                <= thresholds.get("max_loc_per_file", 500),
            ),
        ]
        passed = all(g.passed for g in gates)

        # 5. Findings
        findings: dict[str, list[dict]] = {}
        if dead_code_list:
            findings["dead_code"] = dead_code_list[:30]
        if deep_findings:
            findings["security_deep"] = deep_findings[:20]

        # 6. Summary + Recommendations
        parts = [f"{'✅ PASS' if passed else '❌ FAIL'}"]
        for g in gates:
            if not g.passed:
                parts.append(f"{g.name}={g.actual} (max {g.threshold})")
        if dead_code_list:
            parts.append(f"dead_code={len(dead_code_list)}")
        if deep_findings:
            parts.append(f"deep={len(deep_findings)}")
        if warnings:
            parts.append(f"warnings={len(warnings)}")

        recommendations = self._build_recommendations(
            metrics, gates, findings, thresholds
        )

        return ReviewResult(
            passed=passed,
            metrics=metrics,
            gates=gates,
            findings=findings,
            recommendations=recommendations,
            issues=deep_findings[:50],
            files_scanned=[f.file for f in metrics.files[:50]]
            or [os.path.basename(p) for p in scan_paths[:50]],
            summary=", ".join(parts),
        )

    def _build_recommendations(
        self,
        metrics,
        gates,
        findings,
        thresholds: dict[str, Any],
    ) -> list[dict]:
        """Generate actionable recommendations based on metrics + findings."""
        recs = []
        max_dead = thresholds.get("max_dead_code", 50)

        # Priority 1: Dead code
        dc = findings.get("dead_code", [])
        if dc:
            priv = [f for f in dc if f.get("is_private")]
            pub = [f for f in dc if not f.get("is_private")]
            recs.append(
                {
                    "priority": "P1 - High",
                    "area": "Dead Code",
                    "action": f"Remove {len(priv)} private unused functions (safe). "
                    f"Review {len(pub)} public unused functions: integrate or delete.",
                    "impact": f"Reducing from {len(dc)} to <{max_dead} would pass the gate.",
                    "targets": [
                        {
                            "function": f["function"],
                            "private": f.get("is_private"),
                            "file": f["file"],
                            "line": f["line"],
                        }
                        for f in dc[:10]
                    ],
                }
            )

        # Priority 2: Deep security findings
        sec = findings.get("security_deep", [])
        if sec:
            by_label: dict[str, int] = {}
            for f in sec:
                by_label[f.get("label", f.get("pattern_id", "?"))] = (
                    by_label.get(f.get("label", f.get("pattern_id", "?")), 0) + 1
                )
            top = sorted(by_label.items(), key=lambda x: -x[1])[:5]
            recs.append(
                {
                    "priority": "P2 - High",
                    "area": "Security / Production",
                    "action": f"Revisar {len(sec)} hallazgos de patrones de producción/seguridad. "
                    "Top: " + ", ".join(f"{t}({c})" for t, c in top),
                    "impact": "Reduce riesgo de bugs de producción y vulnerabilidades.",
                    "targets": [{"pattern": t, "count": c} for t, c in top],
                }
            )

        # Priority 3: LOC per file
        loc_gate = next((g for g in gates if g.name == "loc_per_file"), None)
        if loc_gate and not loc_gate.passed:
            recs.append(
                {
                    "priority": "P3 - Medium",
                    "area": "File Size",
                    "action": "Dividir archivos que exceden el límite de LOC por archivo.",
                    "impact": f"Reducing from {loc_gate.actual} to <{loc_gate.threshold} would pass the gate.",
                    "targets": [],
                }
            )

        return recs

    def _resolve_default_paths(self) -> list[str]:
        """Resuelve los paths de código a escanear (repo real del manifest).

        SSOT local via ProjectService.resolve_paths — NO depende del registro
        global.
        """
        try:
            from features.project import ProjectService

            paths = ProjectService.resolve_paths(self.chain_dir)
            repo = paths.repo_path or paths.project_root
            if repo and os.path.isdir(repo):
                base = [repo]
                existing = [p for p in base if os.path.isdir(p)]
                if existing:
                    return self._apply_walker_ignore(existing)
        except Exception as e:
            logger.warning(
                "ProjectService.resolve_paths fallo en review (%s: %s); fallback legacy",
                type(e).__name__, e,
            )
        # Fallback legacy
        from app.path_resolver import resolve_project_root, resolve_source_code_dirs

        proj_dir = resolve_project_root(self.chain_dir)
        project_id = os.path.basename(proj_dir) if proj_dir else ""
        if project_id:
            dirs = resolve_source_code_dirs(project_id)
            paths = [str(d) for d in dirs]
            return self._apply_walker_ignore(paths)
        return [self.chain_dir]

    def _apply_walker_ignore(self, paths: list[str]) -> list[str]:
        """Filtra los paths escaneados contra .walkerignore del proyecto.

        SSOT: el CPG (build_service) ya excluye to_clean_next/, legacy/, etc.
        vía IgnoreRules. El review debe respetar el MISMO contrato, o las
        métricas incluirían código legacy duplicado.
        """
        try:
            from features.project import IgnoreRules

            root = self._repo_root_from_paths(paths)
            if not root:
                return paths
            rules = IgnoreRules(root)
            kept = []
            for p in paths:
                rel = os.path.relpath(p, root) if root else p
                if not rules.is_ignored(rel):
                    kept.append(p)
            return kept
        except Exception as e:
            logger.warning(
                "_apply_walker_ignore fallo (%s: %s); se usan paths sin filtrar",
                type(e).__name__, e,
            )
            return paths

    @staticmethod
    def _repo_root_from_paths(paths: list[str]) -> str:
        """Deriva el repo root (absoluto) como ancestro común de los paths."""
        abs_paths = [p for p in paths if p and os.path.isabs(p)]
        if not abs_paths:
            return ""
        common = os.path.dirname(abs_paths[0])
        for p in abs_paths[1:]:
            while not os.path.realpath(p).startswith(os.path.realpath(common)):
                parent = os.path.dirname(common)
                if parent == common:
                    break
                common = parent
        return common