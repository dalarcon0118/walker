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
            for pat in patterns:
                matches = run_ast_grep(pat["pattern"], filepath, lang)
                if matches:
                    findings.append({
                        "trigger_rule": rule_id,
                        "pattern_id": pat["id"],
                        "label": pat["label"],
                        "severity": pat["severity"],
                        "matches": matches[:10],
                        "count": len(matches),
                    })

    # 2. High-cyclo patterns (triggered by metrics)
    if cyclo > 20:
        for pat in _DEEP_PATTERNS.get("high-cyclo", []):
            matches = run_ast_grep(pat["pattern"], filepath, lang)
            if matches:
                findings.append({
                    "trigger_rule": f"cyclo={cyclo}",
                    "pattern_id": pat["id"],
                    "label": pat["label"],
                    "severity": pat["severity"],
                    "matches": matches[:10],
                    "count": len(matches),
                })

    # 3. Danger patterns (triggered by security issues)
    has_security = checks and any(
        "B1" in c["rule_id"] or "B6" in c["rule_id"] for c in checks
    )
    if has_security:
        for pat in _DEEP_PATTERNS.get("danger", []):
            matches = run_ast_grep(pat["pattern"], filepath, lang)
            if matches:
                findings.append({
                    "trigger_rule": "security-context",
                    "pattern_id": pat["id"],
                    "label": pat["label"],
                    "severity": pat["severity"],
                    "matches": matches[:10],
                    "count": len(matches),
                })

    # 4. TypeScript/JavaScript patterns (triggered by file type)
    if lang in ("typescript", "tsx", "javascript", "jsx"):
        for pat in _DEEP_PATTERNS.get("ts-js", []):
            matches = run_ast_grep(pat["pattern"], filepath, lang)
            if matches:
                findings.append({
                    "trigger_rule": f"lang={lang}",
                    "pattern_id": pat["id"],
                    "label": pat["label"],
                    "severity": pat["severity"],
                    "matches": matches[:10],
                    "count": len(matches),
                })

    # 5. Production-bug patterns (always run, filtered by language)
    for pat in _DEEP_PATTERNS.get("production-bugs", []):
        pat_lang = pat.get("lang")
        if pat_lang and pat_lang != lang:
            continue
        matches = run_ast_grep(pat["pattern"], filepath, lang)
        if matches:
            findings.append({
                "trigger_rule": "production-bug",
                "pattern_id": pat["id"],
                "label": pat["label"],
                "severity": pat["severity"],
                "matches": matches[:10],
                "count": len(matches),
            })

    # 6. Unnecessary-code patterns (always run, filtered by language)
    for pat in _DEEP_PATTERNS.get("unnecessary-code", []):
        pat_lang = pat.get("lang")
        if pat_lang and pat_lang != lang:
            continue
        matches = run_ast_grep(pat["pattern"], filepath, lang)
        if matches:
            findings.append({
                "trigger_rule": "unnecessary-code",
                "pattern_id": pat["id"],
                "label": pat["label"],
                "severity": pat["severity"],
                "matches": matches[:10],
                "count": len(matches),
            })

    return {"deep_findings": findings, "total_deep": len(findings)}
