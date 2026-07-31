"""Wrappers para comandos qlty (metrics, smells, check)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import QLTY, QLTY_AVAILABLE, _METRICS_HEADERS, _FUNC_HEADERS, split_pipe


# ═══════════════════════════════════════════════════════════════
# 1. QLTY METRICS — per-file summary
# ═══════════════════════════════════════════════════════════════

def run_metrics(filepath: Path) -> dict:
    """Run `qlty metrics <file>` and return file-level summary."""
    if not QLTY_AVAILABLE:
        return {}
    try:
        r = subprocess.run(
            [QLTY, "metrics", "--quiet", str(filepath)],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            return {}
        return _parse_metrics_table(r.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}


def _parse_metrics_table(stdout: str) -> dict:
    for line in stdout.splitlines():
        parts = split_pipe(line)
        if len(parts) == len(_METRICS_HEADERS) and parts[0] not in ("name", "TOTAL", ""):
            try:
                funcs = int(parts[2])
                cyclo = int(parts[4])
                return {
                    "classes": int(parts[1]),
                    "funcs": funcs,
                    "fields": int(parts[3]),
                    "cyclo": cyclo,
                    "complexity": int(parts[5]),
                    "lcom": int(parts[6]),
                    "lines": int(parts[7]),
                    "loc": int(parts[8]),
                    "avg_cc": round(cyclo / max(funcs, 1), 2),
                }
            except (ValueError, IndexError):
                continue
    return {}


def run_metrics_functions(filepath: Path) -> list[dict]:
    """Run `qlty metrics --functions <file>` and return per-function list."""
    if not QLTY_AVAILABLE:
        return []
    try:
        r = subprocess.run(
            [QLTY, "metrics", "--functions", "--quiet", str(filepath)],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            return []
        return _parse_func_table(r.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _parse_func_table(stdout: str) -> list[dict]:
    funcs = []
    for line in stdout.splitlines():
        parts = split_pipe(line)
        if len(parts) == len(_FUNC_HEADERS) and parts[0] not in ("function", ""):
            try:
                funcs.append({
                    "name": parts[0].strip(),
                    "fields": int(parts[1]),
                    "cyclo": int(parts[2]),
                    "cognitive": int(parts[3]),
                    "lines": int(parts[4]),
                    "loc": int(parts[5]),
                })
            except (ValueError, IndexError):
                continue
    return funcs


# ═══════════════════════════════════════════════════════════════
# 2. QLTY SMELLS — duplication & code smells
# ═══════════════════════════════════════════════════════════════

def run_smells(target: Path | None = None) -> list[dict]:
    """Run `qlty smells --json` and return structured results."""
    if not QLTY_AVAILABLE:
        return []
    cmd = [QLTY, "smells", "--json"]
    if target:
        cmd.append(str(target))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode not in (0, 1):
            return []
        # Try JSON first (qlty smells --json outputs an array)
        results = _parse_smells_json(r.stdout)
        if results is not None:
            return results
        # Fall back to SARIF
        sarif = _parse_sarif(r.stdout)
        if sarif:
            return sarif
        return []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _parse_smells_json(stdout: str) -> list[dict] | None:
    """Parse qlty smells --json output (array format).
    
    Returns list (possibly empty) on success, None if JSON is not valid array.
    """
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    results = []
    for item in data:
        loc = item.get("location", {})
        results.append({
            "rule_id": f"qlty:{item.get('ruleKey', 'unknown')}",
            "message": item.get("message", ""),
            "level": item.get("level", "LEVEL_INFO").replace("LEVEL_", "").lower(),
            "file": loc.get("path", ""),
            "line": loc.get("range", {}).get("startLine", 0),
            "category": item.get("category", ""),
            "effort_minutes": item.get("effortMinutes", 0),
            "value": item.get("value", 0),
        })
    return results


def _parse_sarif(sarif_text: str) -> list[dict]:
    try:
        data = json.loads(sarif_text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    findings = []
    for run in data.get("runs", []):
        for result in run.get("results", []):
            rule_id = result.get("ruleId", "")
            if not rule_id.startswith("qlty:"):
                continue
            for loc in result.get("locations", []):
                phys = loc.get("physicalLocation", {})
                effort = result.get("properties", {}).get("effortMinutes", 0) or 0
                findings.append({
                    "rule_id": rule_id,
                    "message": result.get("message", {}).get("text", ""),
                    "level": result.get("level", "warning"),
                    "file": phys.get("artifactLocation", {}).get("uri", ""),
                    "line": phys.get("region", {}).get("startLine", 0),
                    "effort_minutes": effort,
                    "value": result.get("message", {}).get("text", "").split("count = ")[-1].split(")")[0]
                        if "count =" in result.get("message", {}).get("text", "") else 0,
                })
    return findings


# ═══════════════════════════════════════════════════════════════
# 3. QLTY CHECK — linter suggestions
# ═══════════════════════════════════════════════════════════════

def run_check(filepaths: list[Path]) -> list[dict]:
    """Run `qlty check --level note --sarif` on given files."""
    if not QLTY_AVAILABLE or not filepaths:
        return []
    paths = [str(f) for f in filepaths if f.exists()]
    if not paths:
        return []
    try:
        r = subprocess.run(
            [QLTY, "check", "--level", "note", "--no-error", "--sarif", "--no-progress"] + paths,
            capture_output=True, text=True, timeout=180,
        )
        if r.returncode not in (0, 1):
            return []
        return _parse_check_sarif(r.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _parse_check_sarif(sarif_text: str) -> list[dict]:
    try:
        data = json.loads(sarif_text)
    except json.JSONDecodeError:
        return []
    issues = []
    for run in data.get("runs", []):
        for result in run.get("results", []):
            rule_id = result.get("ruleId", "")
            level = result.get("level", "note")
            message = result.get("message", {}).get("text", "")
            taxa = [t.get("id", "") for t in result.get("taxa", [])]
            fixes = result.get("fixes", [])
            has_fix = len(fixes) > 0
            fix_desc = fixes[0].get("description", {}).get("text", "") if fixes else ""
            for loc in result.get("locations", []):
                phys = loc.get("physicalLocation", {})
                region = phys.get("region", {})
                issues.append({
                    "rule_id": rule_id, "level": level, "message": message,
                    "file": phys.get("artifactLocation", {}).get("uri", ""),
                    "line": region.get("startLine", 0), "col": region.get("startColumn", 0),
                    "end_line": region.get("endLine", 0), "end_col": region.get("endColumn", 0),
                    "has_fix": has_fix, "fix_description": fix_desc, "taxonomy": taxa,
                })
    return issues


def run_fix(filepaths: list[Path]) -> bool:
    """Auto-apply fixes via `qlty check --fix`."""
    if not QLTY_AVAILABLE or not filepaths:
        return False
    paths = [str(f) for f in filepaths if f.exists()]
    if not paths:
        return False
    try:
        r = subprocess.run(
            [QLTY, "check", "--fix", "--no-progress"] + paths,
            capture_output=True, text=True, timeout=180,
        )
        return r.returncode in (0, 1)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
