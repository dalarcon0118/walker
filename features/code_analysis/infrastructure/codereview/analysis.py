"""Core analysis orchestration — run_analysis, _build_analysis_output."""
from __future__ import annotations

from pathlib import Path

from .config import QLTY_AVAILABLE, AST_GREP_AVAILABLE
from .qlty import run_metrics, run_metrics_functions, run_smells, run_check
from .coupling import coupling_metrics
from .deep import deep_analyze_checks
from .quality import compute_quality_metrics
from .cache import MetricsCache


def find_project_root(start: Path) -> Path:
    """Walk up to find project root (contains .git or .cognitive)."""
    for parent in [start] + list(start.parents):
        if (parent / ".git").exists() or (parent / ".cognitive").exists():
            return parent
    return start.parents[-1] if list(start.parents) else start


def _pick_smells_target(files: list[Path]) -> Path | None:
    """Pick the best target directory for qlty smells (duplication detection)."""
    if not files:
        return None
    if len(files) == 1:
        return files[0].parent
    parents = [f.parent for f in files]
    if len(set(parents)) == 1:
        return parents[0]
    import os
    common = Path(os.path.commonpath([str(p) for p in parents]))
    return common if common != Path(".") else None


def _classify_risk(checks: list[dict], cyclo: int, filepath: Path) -> list[dict]:
    """Classify risk levels from checks and metrics."""
    findings = []
    high_risk = [c for c in checks if c.get("level") in ("error", "warning")]
    if high_risk:
        findings.append({
            "type": "high_risk_checks",
            "count": len(high_risk),
            "file": str(filepath),
        })
    if cyclo > 30:
        findings.append({
            "type": "high_cyclomatic_complexity",
            "value": cyclo,
            "file": str(filepath),
        })
    return findings


def run_analysis(
    files_to_analyze: list[Path],
    mode: str = "deep",
    cache: MetricsCache | None = None,
) -> dict:
    """Run full analysis pipeline.
    
    Args:
        files_to_analyze: List of file paths to analyze
        mode: "deep" (full) or "fast" (metrics only)
        cache: Optional MetricsCache para evitar re-ejecutar qlty en archivos no modificados
    
    Returns:
        Structured dict with results
    """
    target = _pick_smells_target(files_to_analyze)
    smells_target = target if target and target != Path(".") else None

    if mode == "deep":
        all_smells = run_smells(smells_target) if QLTY_AVAILABLE else []
        all_checks = run_check(files_to_analyze) if QLTY_AVAILABLE else []
    else:
        all_smells = []
        all_checks = []

    return _build_analysis_output(files_to_analyze, all_smells, all_checks, mode=mode, cache=cache)


def _build_analysis_output(
    files_to_analyze: list[Path],
    all_smells: list,
    all_checks: list,
    mode: str = "deep",
    cache: MetricsCache | None = None,
) -> dict:
    """Build the structured analysis output dict."""
    results: list[dict] = []
    all_cross_findings = []

    for fp in files_to_analyze:
        if not fp.exists():
            continue

        # Metrics con cache: si ya se computaron en --fast, se reusan
        metrics = cache.get_metrics(fp) if cache else None
        if metrics is None:
            metrics = run_metrics(fp)
            if cache:
                cache.set_metrics(fp, metrics)
        cyclo = metrics.get("cyclo", 0)

        if mode == "deep":
            funcs_data = cache.get_functions(fp) if cache else None
            if funcs_data is None:
                funcs_data = run_metrics_functions(fp)
                if cache:
                    cache.set_functions(fp, funcs_data)
        else:
            funcs_data = []

        # Coupling: multi-lenguaje via ast-grep, Ce only (Ca es muy caro)
        coupl = coupling_metrics(fp, compute_ca=False)

        # Resolve path for matching smells/checks
        fp_str = str(fp)
        rel_path = (
            str(fp.relative_to(find_project_root(fp)))
            if find_project_root(fp)
            else fp_str
        )

        file_smells = [
            s for s in all_smells
            if rel_path in s.get("file", "") or fp_str in s.get("file", "")
        ]
        file_checks = [
            c for c in all_checks
            if c.get("file", "") in (fp_str, rel_path)
            or (rel_path and c.get("file", "").endswith(rel_path))
        ]

        # Cross-reference analysis
        if mode == "deep":
            cross_findings = _classify_risk(file_checks, cyclo, fp)
        else:
            cross_findings = []
        all_cross_findings.extend(cross_findings)

        # Deep analysis (ast-grep)
        if mode == "deep":
            deep = deep_analyze_checks(file_checks, fp, cyclo=cyclo)
        else:
            deep = {"deep_findings": [], "total_deep": 0}

        # Quality metrics
        file_text = fp.read_text() if fp.exists() else ""
        comment_lines_f = sum(
            1 for l in file_text.splitlines()
            if l.strip().startswith("#") or l.strip().startswith("//") or l.strip().startswith("/*")
        )
        file_loc = metrics.get("loc", 0)
        file_qm = (
            compute_quality_metrics(metrics, file_smells, file_checks, file_loc,
                                     len([l for l in file_text.splitlines() if l.strip()]),
                                     comment_lines_f)
            if file_smells or file_checks or mode == "fast"
            else {}
        )

        entry = {
            "file": rel_path,
            "language": fp.suffix or "?",
            "loc": metrics.get("loc", 0),
            "nlok": metrics.get("loc", 0),
            "cyclo": cyclo,
            "complexity": metrics.get("complexity", 0),
            "lcom": metrics.get("lcom", 0),
            "funcs": metrics.get("funcs", 0),
            "functions": funcs_data,
            "ce": coupl["ce"],
            "ca": coupl["ca"],
            "smells_count": len(file_smells),
            "smells": file_smells[:10],
            "checks_count": len(file_checks),
            "checks": [{k: v for k, v in c.items() if k != "file"} for c in file_checks[:20]],
            "cross_findings": cross_findings,
            "deep_analysis": deep,
            "quality": file_qm,
        }
        results.append(entry)

    return {
        "qlty_available": QLTY_AVAILABLE,
        "ast_grep_available": AST_GREP_AVAILABLE,
        "files_analyzed": len(results),
        "files": results,
        "total_smells": sum(f["smells_count"] for f in results),
        "total_checks": len(all_checks),
        "fixable_checks": sum(1 for c in all_checks if c.get("has_fix")),
        "cross_findings": all_cross_findings,
    }
