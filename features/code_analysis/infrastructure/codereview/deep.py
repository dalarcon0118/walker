"""Deep analysis: ast-grep patterns triggered by qlty findings + unconditional scans."""
from __future__ import annotations

from pathlib import Path

from .config import _DEEP_PATTERNS, AST_GREP_AVAILABLE
from .ast_grep import run_ast_grep, ast_grep_lang


def _get_deep_patterns_for_rule(rule_id: str) -> list[dict]:
    """Return ast-grep patterns relevant to a given qlty rule ID."""
    for key, patterns in _DEEP_PATTERNS.items():
        if key in rule_id:
            return patterns
    return []


def _append_pattern_findings(
    findings: list[dict],
    patterns: list[dict],
    filepath: Path,
    lang: str,
    trigger_rule: str,
) -> None:
    """Corre ast-grep para cada patron y agrega hallazgos."""
    for pat in patterns:
        pat_lang = pat.get("lang")
        if pat_lang and pat_lang != lang:
            continue
        matches = run_ast_grep(pat["pattern"], filepath, lang)
        if matches:
            findings.append({
                "trigger_rule": trigger_rule,
                "pattern_id": pat["id"],
                "label": pat["label"],
                "severity": pat["severity"],
                "matches": matches[:10],
                "count": len(matches),
            })


def deep_analyze_checks(checks: list[dict], filepath: Path, cyclo: int = 0) -> dict:
    """Correlate qlty findings with ast-grep structural queries.

    For each unique rule type found by qlty, run ast-grep patterns
    that deepen the analysis (classify exception types, find nested
    structures, locate exact unused imports, etc.).
    """
    if not AST_GREP_AVAILABLE:
        return {}

    lang = ast_grep_lang(filepath)
    if not lang:
        return {}

    findings = []

    # 1. Deep patterns triggered by specific qlty rules
    if checks:
        rule_types = set(c["rule_id"] for c in checks)
        for rule_id in rule_types:
            patterns = _get_deep_patterns_for_rule(rule_id)
            _append_pattern_findings(findings, patterns, filepath, lang, rule_id)

    # 2. High-cyclo patterns (triggered by metrics)
    if cyclo > 20:
        _append_pattern_findings(
            findings, _DEEP_PATTERNS.get("high-cyclo", []), filepath, lang,
            f"cyclo={cyclo}",
        )

    # 3. Danger patterns (triggered by security issues)
    has_security = checks and any(
        "B1" in c["rule_id"] or "B6" in c["rule_id"] for c in checks
    )
    if has_security:
        _append_pattern_findings(
            findings, _DEEP_PATTERNS.get("danger", []), filepath, lang,
            "security-context",
        )

    # 4. TypeScript/JavaScript patterns (triggered by file type)
    if lang in ("typescript", "tsx", "javascript", "jsx"):
        _append_pattern_findings(
            findings, _DEEP_PATTERNS.get("ts-js", []), filepath, lang,
            f"lang={lang}",
        )

    # 5. Production-bug patterns (always run, filtered by language)
    _append_pattern_findings(
        findings, _DEEP_PATTERNS.get("production-bugs", []), filepath, lang,
        "production-bug",
    )

    # 6. Unnecessary-code patterns (always run, filtered by language)
    _append_pattern_findings(
        findings, _DEEP_PATTERNS.get("unnecessary-code", []), filepath, lang,
        "unnecessary-code",
    )

    return {"deep_findings": findings, "total_deep": len(findings)}
