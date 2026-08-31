"""Core analysis orchestration — run_analysis, _build_analysis_output.

Sin binario externo qlty (ADR-WALKER-054): las métricas numéricas básicas
(LOC, funciones) se derivan del propio archivo; el valor estructural viene
de ast-grep (coupling + deep patterns). Nunca invoca subprocesos de qlty.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import AST_GREP_AVAILABLE
from .coupling import coupling_metrics
from .deep import deep_analyze_file
from .quality import compute_quality_metrics

_FUNC_RE = re.compile(
    r"^\s*(?:def|class|async\s+def|function|const\s+\w+\s*=\s*(?:async\s*)?\()",
    re.MULTILINE,
)


def find_project_root(start: Path) -> Path:
    """Walk up to find project root (contains .git or .cognitive)."""
    for parent in [start] + list(start.parents):
        if (parent / ".git").exists() or (parent / ".cognitive").exists():
            return parent
    return start.parents[-1] if list(start.parents) else start


def _count_functions(text: str) -> int:
    """Conteo ligero de declaraciones de funciones/clases por regex."""
    if not text:
        return 0
    return len(_FUNC_RE.findall(text))


def _classify_risk(checks: list[dict], cyclo: int, filepath: Path) -> list[dict]:
    """Classify risk levels from deep findings and metrics."""
    findings = []
    high_risk = [c for c in checks if c.get("severity") in ("critical", "high")]
    if high_risk:
        findings.append({
            "type": "high_risk_deep",
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
    cache: object | None = None,
) -> dict:
    """Run full analysis pipeline (ast-grep, sin qlty).

    Args:
        files_to_analyze: List of file paths to analyze
        mode: "deep" (full) or "fast" (metrics only)
        cache: se conserva por compatibilidad de firma; sin caché externo

    Returns:
        Structured dict with results
    """
    return _build_analysis_output(files_to_analyze, mode=mode)


def _build_analysis_output(
    files_to_analyze: list[Path],
    mode: str = "deep",
    cache: object | None = None,
) -> dict:
    """Build the structured analysis output dict (sin qlty)."""
    results: list[dict] = []
    all_cross_findings = []

    for fp in files_to_analyze:
        if not fp.exists():
            continue

        # Métricas numéricas desde el propio archivo (LOC, funciones)
        try:
            file_text = fp.read_text(errors="replace")
        except OSError:
            file_text = ""
        raw_lines = file_text.splitlines()
        non_empty = [l for l in raw_lines if l.strip()]
        loc = len(non_empty)
        funcs = _count_functions(file_text)
        cyclo = 0  # sin AST profundo: se reporta como no disponible

        # Coupling: multi-lenguaje via ast-grep, Ce only (Ca es muy caro)
        coupl = coupling_metrics(fp, compute_ca=False)

        # Resolve path for the entry
        fp_str = str(fp)
        root = find_project_root(fp)
        rel_path = (
            str(fp.relative_to(root)) if root else fp_str
        )

        # Deep analysis (ast-grep patterns, incondicional)
        if mode == "deep":
            deep = deep_analyze_file(fp)
            cross_findings = _classify_risk(
                deep.get("deep_findings", []), cyclo, fp
            )
        else:
            deep = {"deep_findings": [], "total_deep": 0}
            cross_findings = []
        all_cross_findings.extend(cross_findings)

        # Quality metrics (desde datos locales, sin linter externo)
        comment_lines = sum(
            1 for l in raw_lines
            if l.strip().startswith("#")
            or l.strip().startswith("//")
            or l.strip().startswith("/*")
        )
        metrics_basic = {
            "loc": loc,
            "cyclo": cyclo,
            "complexity": 0,
            "classes": 0,
            "fields": 0,
            "funcs": funcs,
        }
        file_qm = compute_quality_metrics(
            metrics_basic, [], deep.get("deep_findings", []), loc,
            len(raw_lines), comment_lines,
        ) if (deep.get("deep_findings") or mode == "fast") else {}

        entry = {
            "file": rel_path,
            "language": fp.suffix or "?",
            "loc": loc,
            "nlok": loc,
            "cyclo": cyclo,
            "complexity": 0,
            "lcom": 0,
            "funcs": funcs,
            "functions": [],
            "ce": coupl["ce"],
            "ca": coupl["ca"],
            "smells_count": 0,
            "smells": [],
            "checks_count": len(deep.get("deep_findings", [])),
            "checks": deep.get("deep_findings", [])[:20],
            "cross_findings": cross_findings,
            "deep_analysis": deep,
            "quality": file_qm,
        }
        results.append(entry)

    return {
        "ast_grep_available": AST_GREP_AVAILABLE,
        "files_analyzed": len(results),
        "files": results,
        "total_smells": 0,
        "total_checks": sum(f["checks_count"] for f in results),
        "fixable_checks": 0,
        "cross_findings": all_cross_findings,
    }