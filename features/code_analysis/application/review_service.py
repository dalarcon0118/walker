"""application/review_service.py — Code review use case.

Usa qlty para métricas numéricas (cyclo, LOC, smells, lint).
Usa CPG para análisis estructural (dead code, call graph).
Todo en batch, no por archivo.

Return pattern: las operaciones fallibles dejan de tragarse errores con
`except: pass`; los fallos se loggean (convention-001: nunca silencioso).
"""

from __future__ import annotations
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

from features.code_analysis.domain.graph import Graph
from features.code_analysis.domain.metrics import ReviewResult, ProjectMetrics
from features.code_analysis.infrastructure.repository import CpgFileRepository
from features.code_analysis.infrastructure.metrics import (
    run_metrics,
    run_function_metrics,
    run_smells,
    run_check,
    compute_file_metrics,
    check_quality_gates,
)
from features.code_analysis.infrastructure.metrics_cache import IncrementalMetricsCache


class ReviewUseCase:
    """Code review: metrics + quality gates + smells + lint."""

    def __init__(
        self,
        chain_dir: str,
        project_id: str = "",
        scan_paths: list[str] | None = None,
        data_dir: str | None = None,
        cache: "IncrementalMetricsCache | None" = None,
    ):
        self.chain_dir = chain_dir
        self._project_id = project_id
        self._scan_paths = scan_paths
        self._data_dir = data_dir
        self._cache = cache
        self._graph: Graph | None = None
        self._repo: CpgFileRepository | None = None

    @property
    def repo(self) -> CpgFileRepository:
        if self._repo is None:
            if self._data_dir:
                self._repo = CpgFileRepository(self._data_dir)
            else:
                from libs.path_resolver import resolve_project_root, get_data_dir

                project_root = resolve_project_root(self.chain_dir)
                project_id = os.path.basename(project_root) if project_root else ""
                data_dir = str(get_data_dir(project_id)) if project_id else ""
                self._repo = CpgFileRepository(
                    data_dir or os.path.join(project_root, "data")
                    if project_root
                    else ""
                )
        return self._repo

    @property
    def graph(self) -> Graph:
        if self._graph is None:
            self._graph = self.repo.load_graph(self._project_id)
        return self._graph

    @property
    def _qlty_cwd(self) -> str:
        """Directorio de trabajo para qlty = repo real del código (donde vive
        .qlty/), NO el data_dir del proyecto.

        chain_dir puede apuntar al data_dir (.cognitive/projects/<pid>), donde
        qlty no encuentra su config → metrics devuelve 0 files. Derivamos el
        repo desde los source dirs absolutos (o del primer path escaneado).
        """
        try:
            paths = self._resolve_default_paths()
            abs_paths = [p for p in paths if p and os.path.isabs(p)]
            if abs_paths:
                # Ancestro común de todos los source dirs = repo root
                common = os.path.dirname(abs_paths[0])
                for p in abs_paths[1:]:
                    while not (os.path.realpath(p)).startswith(os.path.realpath(common)):
                        parent = os.path.dirname(common)
                        if parent == common:
                            break
                        common = parent
                if os.path.isdir(os.path.join(common, ".qlty")):
                    return common
                # Si no hay .qlty en el ancestro, subir hasta encontrarlo
                cur = common
                while cur and cur != os.path.dirname(cur):
                    if os.path.isdir(os.path.join(cur, ".qlty")):
                        return cur
                    cur = os.path.dirname(cur)
                return common
        except Exception as e:
            logger.warning(
                "No se pudo resolver el cwd de qlty (%s: %s); fallback a chain_dir",
                type(e).__name__, e,
            )
        return self.chain_dir

    def _build_per_file_metrics(
        self,
        qlty_metrics: list[dict],
        qlty_funcs: dict[str, list[dict]],
        graph: Graph,
        thresholds: dict[str, Any],
    ) -> tuple[list, list]:
        """Construye métricas por archivo + funciones de alta complejidad."""
        per_file = []
        high_cyclo_funcs = []
        cyclo_threshold = thresholds.get("max_cyclo", 10)

        for qf in qlty_metrics:
            fp = qf.get("file", "")
            # Normalizar path: qlty usa paths relativos al repo, CPG usa absolutos
            if os.path.isabs(fp):
                abs_fp = os.path.realpath(fp)
            else:
                abs_fp = os.path.realpath(os.path.join(self.chain_dir, fp))
            funcs = qlty_funcs.get(fp, qlty_funcs.get(abs_fp, []))
            fm = compute_file_metrics(fp, qf, funcs, graph)
            per_file.append(fm)

            for fn in funcs:
                cyclo = fn.get("cyclo", 0)
                if cyclo > cyclo_threshold:
                    high_cyclo_funcs.append(
                        self._enrich_high_cyclo(fn, fp, graph)
                    )
        return per_file, high_cyclo_funcs

    @staticmethod
    def _enrich_high_cyclo(fn: dict, fp: str, graph: Graph) -> dict:
        """Enriquece una funcion de alta complejidad con contexto del CPG."""
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
        return {
            "function": fn["function"],
            "file": fp,
            "line": fn.get("line", 0),
            "cyclo": fn.get("cyclo", 0),
            "cognitive": fn.get("cognitive", 0),
            "lines": fn.get("lines", 0),
            "loc": fn.get("loc", 0),
            "callers": callers[:10],
            "callees": callees[:15],
        }

    def _scan_dead_code(
        self,
        graph: Graph,
        scanner: DeadCodeReferenceScanner,
        is_false_positive_dead_code,
    ) -> list[dict]:
        """Detecta dead code real desde el CPG con filtros de falsos positivos."""
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
            except (OSError, IOError) as e:
                logger.debug(
                    "No se pudo leer %s para main-guard check (%s); se asume sin guard",
                    filepath, e,
                )
                _main_guard_cache[filepath] = False
                return False

        for nid, node in graph.nodes.items():
            if node.type not in ("function", "method"):
                continue
            incoming = [ie for ie in graph.get_incoming(nid) if ie["type"] == "CALLS"]
            if incoming or node.name.startswith("__"):
                continue
            if is_false_positive_dead_code(
                node.name, node.type, node.signature, node.path
            ):
                continue
            if scanner.is_referenced(node.name, node.path, node.line):
                continue
            # Skip functions named 'main' in files with __name__ guard (CLI entry points)
            if node.name == "main" and _has_main_guard(node.path):
                continue
            dead_code_list.append(
                {
                    "function": node.name,
                    "file": node.path,
                    "line": node.line,
                    "is_private": node.name.startswith("_"),
                }
            )
        return dead_code_list

    def review_files(
        self, files: list[str] | None = None, thresholds: dict[str, Any] | None = None
    ) -> ReviewResult:
        """Code review: metrics + quality gates + smells + lint.

        Todo en batch via qlty + CPG.
        """
        graph = self.graph
        scan_paths = files or self._scan_paths or self._resolve_default_paths()

        # 1. Métricas qlty con caché incremental (si está disponible).
        #    Solo re-ejecuta qlty sobre archivos nuevos/modificados; el resto
        #    se reusa del cache por mtime/size. Ahorro ~4 invocaciones qlty
        #    cuando el código no cambió.
        if self._cache is not None:
            qlty_metrics, qlty_funcs, smells, lint_issues, cache_stats = (
                self._run_with_cache(scan_paths)
            )
        else:
            # Flujo original: batch completo (los wrappers loggean sus fallos)
            qlty_metrics = run_metrics(scan_paths, cwd=self._qlty_cwd)
            qlty_funcs = run_function_metrics(scan_paths, cwd=self._qlty_cwd)
            smells = run_smells(scan_paths, cwd=self._qlty_cwd)
            lint_issues = run_check(scan_paths, cwd=self._qlty_cwd)

        # 2. Build per-file metrics
        per_file, high_cyclo_funcs = self._build_per_file_metrics(
            qlty_metrics, qlty_funcs, graph, thresholds or {}
        )

        # 3. Dead code from CPG + filtro de falsos positivos
        #    (fields de dataclass/ODM, test_*, dunders de framework) y
        #    detección de referencias por texto (registry dinámico,
        #    imports lazy, dispatch intra-archivo).
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

        # 4. Smells + lint ya resueltos arriba (con o sin cache)

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

    def _run_with_cache(self, scan_paths: list[str]) -> tuple:
        """Corre métricas qlty usando el caché incremental.

        Devuelve (qlty_metrics, qlty_funcs, smells, lint, cache_stats):
          - qlty_metrics: list[dict] con file/cyclo/... para todos los archivos
          - qlty_funcs: dict {filepath: [funcs]} para todos los archivos
          - smells/lint: list[dict] de issues SARIF (todos los archivos)
        """
        cache = self._cache
        assert cache is not None
        fresh, stale = cache.get_stale_files(scan_paths)

        # 1. Correr qlty SOLO sobre los stale (nuevos/modificados)
        new_metrics: list[dict] = []
        new_funcs_map: dict[str, list[dict]] = {}
        new_smells: list[dict] = []
        new_lint: list[dict] = []

        if stale:
            new_metrics = run_metrics(stale, cwd=self._qlty_cwd)
            new_funcs_map = run_function_metrics(stale, cwd=self._qlty_cwd)
            new_smells = run_smells(stale, cwd=self._qlty_cwd)
            new_lint = run_check(stale, cwd=self._qlty_cwd)

        # 2. Actualizar cache por archivo. qlty devuelve paths RELATIVOS al
        #    repo (cwd). Se normalizan a absolutos respecto al repo para que
        #    el key del cache coincida con _file_key (realpath).
        def _abs(fp: str) -> str:
            if os.path.isabs(fp):
                return os.path.realpath(fp)
            return os.path.realpath(os.path.join(self.chain_dir, fp))

        metrics_by_file: dict[str, dict] = {}
        for m in new_metrics:
            fp = _abs(m.get("file", ""))
            metrics_by_file[fp] = m
            cache.update(fp, metrics=m)
        for fp, funcs in new_funcs_map.items():
            cache.update(_abs(fp), functions=funcs)
        # Agrupar smells/lint por archivo ANTES de cachear (el cache guarda por
        # archivo; cada update reemplaza — si no agrupamos, se pierden issues).
        smells_by_file: dict[str, list[dict]] = {}
        for s in new_smells:
            smells_by_file.setdefault(_abs(s.get("file", "")), []).append(s)
        lint_by_file: dict[str, list[dict]] = {}
        for l_ in new_lint:
            lint_by_file.setdefault(_abs(l_.get("file", "")), []).append(l_)
        for fp, issues in smells_by_file.items():
            cache.update(fp, smells=issues)
        for fp, issues in lint_by_file.items():
            cache.update(fp, lint=issues)

        # 3. Merge: fresh (de cache) + stale (nuevos)
        all_files = list(dict.fromkeys(fresh + stale))
        qlty_metrics: list[dict] = []
        qlty_funcs: dict[str, list[dict]] = {}
        smells: list[dict] = []
        lint_issues: list[dict] = []

        for fp in all_files:
            entry = cache.get(fp)
            if entry:
                if entry.get("metrics"):
                    qlty_metrics.append(entry["metrics"])
                if entry.get("functions"):
                    qlty_funcs[fp] = entry["functions"]
                smells.extend(entry.get("smells", []) or [])
                lint_issues.extend(entry.get("lint", []) or [])

        cache.save()
        return qlty_metrics, qlty_funcs, smells, lint_issues, cache.stats

    def _build_recommendations(
        self, metrics, gates, findings, smells, lint_issues, thresholds
    ):
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
                strategies.append(
                    "Extraer bloques condicionales anidados a funciones helper"
                )
            if top.get("callees", []):
                strategies.append(
                    f"Divide en {len(top['callees'])} llamadas más pequeñas y modulares"
                )
            if top.get("callers"):
                strategies.append(
                    "Revisar si acepta太多 parámetros o responsabilidades"
                )
            strategies.append(
                "Reemplazar cadenas if-elif con dict dispatch o pattern matching"
            )
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
                file_context.append(
                    {
                        "file": fp.split("/projects/delta-skill/")[-1]
                        if "/projects/delta-skill/" in fp
                        else fp,
                        "high_cyclo_funcs": funcs_in_file,
                        "total_cyclo": total,
                    }
                )

            recs.append(
                {
                    "priority": "P1 - Critical",
                    "area": "Complexity",
                    "action": f"Refactor high-complexity functions. Top target: {top['function']} (cyclo={top['cyclo']}, {top.get('lines', 0)} lines). "
                    f"Called by {len(top.get('callers', []))} functions, calls {len(top.get('callees', []))} functions.",
                    "impact": f"Reducing cyclo from {metrics.cyclo_max} to <{max_cyclo} would pass the gate.",
                    "strategies": strategies,
                    "files_most_affected": file_context,
                    "targets": [
                        {
                            "function": f["function"],
                            "cyclo": f["cyclo"],
                            "cognitive": f.get("cognitive", 0),
                            "lines": f.get("lines", 0),
                            "file": f["file"].split("/projects/delta-skill/")[-1]
                            if "/projects/delta-skill/" in f["file"]
                            else f["file"],
                            "callers": f.get("callers", [])[:5],
                            "callees": f.get("callees", [])[:8],
                        }
                        for f in hc[:5]
                    ],
                }
            )

        # Priority 2: Dead code
        dc = findings.get("dead_code", [])
        if dc:
            priv = [f for f in dc if f.get("is_private")]
            pub = [f for f in dc if not f.get("is_private")]
            recs.append(
                {
                    "priority": "P2 - High",
                    "area": "Dead Code",
                    "action": f"Remove {len(priv)} private unused functions (safe). "
                    f"Review {len(pub)} public unused functions: integrate or delete.",
                    "impact": f"Reducing from {len(dc)} to <{max_dead} would pass the gate.",
                    "targets": [
                        {
                            "function": f["function"],
                            "private": f.get("is_private"),
                            "file": f["file"].split("/projects/delta-skill/")[-1],
                            "line": f["line"],
                        }
                        for f in dc[:10]
                    ],
                }
            )

        # Priority 3: Smells
        if smells:
            by_type = {}
            for s in smells:
                tid = s["id"].split(":")[-1] if ":" in s["id"] else s["id"]
                by_type[tid] = by_type.get(tid, 0) + 1
            top_smells = sorted(by_type.items(), key=lambda x: -x[1])[:5]
            recs.append(
                {
                    "priority": "P3 - Medium",
                    "area": "Code Smells",
                    "action": f"Fix {len(smells)} code smells. Top types: "
                    + ", ".join(f"{t}({c})" for t, c in top_smells),
                    "impact": "Reduces technical debt, improves maintainability.",
                    "targets": [{"type": t, "count": c} for t, c in top_smells],
                }
            )

        # Priority 4: Lint
        if lint_issues:
            by_linter = {}
            for l in lint_issues:
                lid = l["id"].split(":")[0] if ":" in l["id"] else "other"
                by_linter[lid] = by_linter.get(lid, 0) + 1
            top_linters = sorted(by_linter.items(), key=lambda x: -x[1])[:5]
            recs.append(
                {
                    "priority": "P4 - Low",
                    "area": "Linting",
                    "action": f"Fix {len(lint_issues)} linter issues. Auto-fix with: qlty check --fix",
                    "impact": "Enforces code style, catches bugs early.",
                    "targets": [{"linter": t, "count": c} for t, c in top_linters],
                }
            )

        return recs

    def _resolve_default_paths(self) -> list[str]:
        """Resuelve los paths de código a escanear (repo real del manifest).

        SSOT local via ProjectService.resolve_paths — NO depende del registro
        global (fallaba: devolvía el dir cognitivo sin código → qlty 0 files).
        """
        try:
            from features.project import ProjectService

            paths = ProjectService.resolve_paths(self.chain_dir)
            repo = paths.repo_path or paths.project_root
            if repo and os.path.isdir(repo):
                base = [repo]
                if paths.code_paths:
                    base = [os.path.join(repo, p) for p in paths.code_paths]
                existing = [p for p in base if os.path.isdir(p)]
                if existing:
                    return self._apply_walker_ignore(existing)
        except Exception as e:
            logger.warning(
                "ProjectService.resolve_paths fallo en review (%s: %s); fallback legacy",
                type(e).__name__, e,
            )
        # Fallback legacy
        from libs.path_resolver import resolve_project_root, resolve_source_code_dirs

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
        vía IgnoreRules. El review (qlty) debe respetar el MISMO contrato, o
        las métricas incluirían código legacy duplicado (to_clean_next/...)
        que infla cyclo_max, smells y lint.
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
