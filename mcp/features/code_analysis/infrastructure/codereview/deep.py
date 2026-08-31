"""Deep analysis: ast-grep patterns (sin qlty).

Antes se correlacionaban reglas de qlty con patrones ast-grep; con la
eliminación del binario externo (ADR-WALKER-054), el deep analysis corre
patrones de producción/seguridad/código-innecesario de forma incondicional
(filtrados por lenguaje).
"""
from __future__ import annotations

from pathlib import Path

from .config import _DEEP_PATTERNS, AST_GREP_AVAILABLE
from .ast_grep import run_ast_grep, ast_grep_lang


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


def deep_analyze_file(filepath: Path) -> dict:
    """Deep analysis ast-grep incondicional (sin reglas de qlty).

    Corre los grupos danger / production-bugs / unnecessary-code / ts-js
    filtrados por lenguaje del archivo. Nunca lanza.
    """
    if not AST_GREP_AVAILABLE:
        return {}

    lang = ast_grep_lang(filepath)
    if not lang:
        return {}

    findings = []

    # 1. Danger / security patterns
    _append_pattern_findings(
        findings, _DEEP_PATTERNS.get("danger", []), filepath, lang,
        "danger",
    )

    # 2. Production-bug patterns (always run, filtered by language)
    _append_pattern_findings(
        findings, _DEEP_PATTERNS.get("production-bugs", []), filepath, lang,
        "production-bug",
    )

    # 3. Unnecessary-code patterns (always run, filtered by language)
    _append_pattern_findings(
        findings, _DEEP_PATTERNS.get("unnecessary-code", []), filepath, lang,
        "unnecessary-code",
    )

    # 4. TypeScript/JavaScript patterns (triggered by file type)
    if lang in ("typescript", "tsx", "javascript", "jsx"):
        _append_pattern_findings(
            findings, _DEEP_PATTERNS.get("ts-js", []), filepath, lang,
            f"lang={lang}",
        )

    return {"deep_findings": findings, "total_deep": len(findings)}


def deep_analyze_checks(checks: list[dict], filepath: Path, cyclo: int = 0) -> dict:
    """Versión CLI (codereview): deep analysis sin correlación qlty.

    `checks` se conserva por compatibilidad de firma pero ya no dispara
    patrones (no hay linter externo). El análisis es incondicional.
    """
    return deep_analyze_file(filepath)