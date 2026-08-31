"""Quality metrics computation — maintainability, security, formatting."""
from __future__ import annotations

from .config import _MAINTAINABILITY_THRESHOLDS, _SECURITY_THRESHOLDS, _ESTIMATED_MINUTES_PER_LOC


def maintainability_rating(ratio: float) -> str:
    for threshold, rating in _MAINTAINABILITY_THRESHOLDS:
        if ratio <= threshold:
            return rating
    return "E"


def security_rating(critical_or_high: int) -> str:
    for threshold, rating in _SECURITY_THRESHOLDS:
        if critical_or_high <= threshold:
            return rating
    return "E"


def c_lower_better(label: str, value: int, yellow: int = 10, red: int = 30) -> str:
    """Color helper for lower-is-better metrics."""
    if value >= red:
        return f"\033[31m{label}={value}\033[0m"
    elif value >= yellow:
        return f"\033[33m{label}={value}\033[0m"
    else:
        return f"\033[32m{label}={value}\033[0m"


def format_rating(rating: str) -> str:
    colors = {"A": "\033[32m", "B": "\033[33m", "C": "\033[33m", "D": "\033[31m", "E": "\033[31m"}
    return f"{colors.get(rating, '')}{rating}\033[0m"


def compute_quality_metrics(
    metrics: dict,
    smells: list,
    checks: list,
    nloc: int,
    raw_lines: int = 0,
    comment_lines: int = 0,
) -> dict:
    """Compute quality metrics from local analysis data (sin qlty)."""
    cyclo = metrics.get("cyclo", 0)
    complexity_n = metrics.get("complexity", 0)
    classes = metrics.get("classes", 0)
    fields = metrics.get("fields", 0)
    funcs = metrics.get("funcs", 0)

    dup_lines = sum(s.get("value", 0) for s in smells if "duplication" in s.get("category", "").lower())
    dup_blocks = sum(1 for s in smells if "duplication" in s.get("category", "").lower())
    dup_density = round(dup_lines / max(nloc, 1), 4)
    cyclo_density = round(cyclo / max(nloc, 1), 4)
    complexity_density = round(complexity_n / max(nloc, 1), 4)
    comments_density = round(comment_lines / max(raw_lines, 1), 4)

    tech_debt_smells = sum(s.get("effort_minutes", 0) for s in smells)
    tech_debt_checks = sum(10 for c in checks if c.get("level") in ("error", "warning"))
    tech_debt = tech_debt_smells + tech_debt_checks
    
    est_impl_minutes = nloc * _ESTIMATED_MINUTES_PER_LOC
    tech_debt_ratio = round(tech_debt / max(int(est_impl_minutes), 1), 4)

    security_high = sum(1 for c in checks if "critical" in c.get("level", "") or "error" in c.get("level", ""))
    security_medium = sum(1 for c in checks if "warning" in c.get("level", ""))
    security_low = sum(1 for c in checks if "note" in c.get("level", ""))
    security_issues = security_high + security_medium + security_low

    return {
        "classes": classes,
        "fields": fields,
        "functions": funcs,
        "code_files": 1,
        "lines": raw_lines or nloc,
        "nloc": nloc,
        "comment_lines": comment_lines,
        "comments_density": comments_density,
        "dup_blocks": dup_blocks,
        "dup_lines": dup_lines,
        "dup_density": dup_density,
        "cyclomatic_complexity": cyclo,
        "cognitive_complexity": complexity_n,
        "cyclo_density": cyclo_density,
        "complexity_density": complexity_density,
        "smells": len(smells),
        "tech_debt_minutes": tech_debt,
        "tech_debt_hours": round(tech_debt / 60, 1),
        "tech_debt_ratio": tech_debt_ratio,
        "maintainability_rating": maintainability_rating(tech_debt_ratio),
        "security_issues": security_issues,
        "security_high": security_high,
        "security_medium": security_medium,
        "security_low": security_low,
        "security_rating": security_rating(security_high),
        "issues": len(checks),
        "issues_high": security_high,
        "issues_medium": security_medium,
        "issues_low": security_low,
        "fixable_issues": sum(1 for c in checks if c.get("has_fix")),
        "coverage_pct": None,
        "coverage_rating": None,
    }
