"""infrastructure/metrics.py — Code metrics via qlty CLI + CPG.

Usa qlty para métricas numéricas (cyclo, LOC, complexity) y smells.
Usa CPG para análisis estructural (dead code, call graph).

qlty commands (all batch, not per-file):
  qlty metrics --quiet <paths>              → file-level metrics
  qlty metrics --functions --quiet <paths>  → per-function metrics
  qlty smells --sarif <paths>               → code smells JSON
  qlty check --level medium --sarif <paths> → linter issues JSON

Result pattern (dry-python/returns): los cores puros con @safe nunca lanzan;
los wrappers deciden con is_successful y hacen fallback explícito con log
(convention-001: nunca except silencioso).
"""
from __future__ import annotations
import json
import logging
import os
import re
import subprocess
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

from features.code_analysis.domain.graph import Graph
from features.code_analysis.domain.metrics import FileMetrics, ProjectMetrics, QualityGate
from features.dependency_checker import ensure_tools

logger = logging.getLogger(__name__)

QLTY_BIN = os.environ.get("QLTY_BIN", "qlty")
_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _strip(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _split(line: str) -> list[str]:
    return [p.strip() for p in _strip(line).split("|")]


# ── 1. File-level metrics (batch) ──────────────────────────────

def run_metrics(paths: list[str], cwd: str | None = None) -> list[dict]:
    """Run qlty metrics on multiple paths in one invocation.

    Args:
        paths: Paths a medir (absolutos o relativos a cwd).
        cwd: Directorio de trabajo para qlty (repo donde vive .qlty/). Necesario
             para que qlty encuentre su config cuando el proceso corre desde
             otro directorio (ej. el repo del walker).

    Returns:
        [{file, classes, funcs, fields, cyclo, complex, lcom, lines, loc}, ...]

    Result pattern: el core @safe nunca lanza; ante fallo devuelve []
    y loggea el error (convention-001).
    """
    result = _run_metrics_core(paths, cwd)
    if is_successful(result):
        return result.value_or([])
    error = result.failure()
    logger.error(
        "qlty metrics fallo (%s: %s); sin métricas",
        type(error).__name__, error,
    )
    return []


@safe
def _run_metrics_core(paths: list[str], cwd: str | None) -> list[dict]:
    """Core puro de run_metrics (puede lanzar; @safe captura y loggea)."""
    ensure_tools(["qlty"])
    r = subprocess.run(
        [QLTY_BIN, "metrics", "--quiet"] + paths,
        capture_output=True, text=True, timeout=60, cwd=cwd,
    )
    if r.returncode != 0:
        raise RuntimeError(f"qlty metrics exit {r.returncode}: {r.stderr[:200]}")
    return _parse_metrics(r.stdout)


def _parse_metrics(stdout: str) -> list[dict]:
    """Parsea la tabla de métricas de qlty.

    Las líneas malformadas se omiten (filtrado de datos externos, no un
    error del sistema) — se loggea a debug para no ser silencioso.
    """
    results = []
    for line in _strip(stdout).split("\n"):
        if "|" not in line or "TOTAL" in line:
            continue
        clean = line.strip()
        if not clean or clean.startswith("name") or set(clean).issubset({"|", "-", "+", " "}):
            continue
        parts = _split(line)
        if len(parts) >= 9:
            try:
                results.append({
                    "file": parts[0],
                    "classes": int(parts[1]) if parts[1].isdigit() else 0,
                    "funcs": int(parts[2]) if parts[2].isdigit() else 0,
                    "fields": int(parts[3]) if parts[3].isdigit() else 0,
                    "cyclo": int(parts[4]) if parts[4].isdigit() else 0,
                    "complex": int(parts[5]) if parts[5].isdigit() else 0,
                    "lcom": int(parts[6]) if parts[6].isdigit() else 0,
                    "lines": int(parts[7]) if parts[7].isdigit() else 0,
                    "loc": int(parts[8]) if parts[8].isdigit() else 0,
                })
            except (ValueError, IndexError):
                logger.debug("línea de métricas qlty no parseable, se omite: %s", clean[:120])
    return results


# ── 2. Per-function metrics (batch) ────────────────────────────

def run_function_metrics(
    paths: list[str], cwd: str | None = None
) -> dict[str, list[dict]]:
    """Run qlty metrics --functions on multiple paths.

    Returns {filepath: [{function, cyclo, cognitive, lines, loc}, ...]}

    Result pattern: el core @safe nunca lanza; ante fallo devuelve {}
    y loggea el error (convention-001).
    """
    result = _run_function_metrics_core(paths, cwd)
    if is_successful(result):
        return result.value_or({})
    error = result.failure()
    logger.error(
        "qlty metrics --functions fallo (%s: %s); sin funciones",
        type(error).__name__, error,
    )
    return {}


@safe
def _run_function_metrics_core(paths: list[str], cwd: str | None) -> dict[str, list[dict]]:
    """Core puro de run_function_metrics (puede lanzar; @safe captura)."""
    ensure_tools(["qlty"])
    r = subprocess.run(
        [QLTY_BIN, "metrics", "--functions", "--quiet"] + paths,
        capture_output=True, text=True, timeout=60, cwd=cwd,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"qlty metrics --functions exit {r.returncode}: {r.stderr[:200]}"
        )
    return _parse_function_metrics(r.stdout)


def _parse_function_metrics(stdout: str) -> dict[str, list[dict]]:
    """Parsea la tabla per-function de qlty (filtrado, nunca lanza)."""
    results: dict[str, list[dict]] = {}
    current_file = ""
    for line in _strip(stdout).split("\n"):
        stripped = line.strip()
        # Empty line: no hace nada (no resetea current_file)
        if not stripped:
            continue
        # File header: no pipe, no es separador
        if "|" not in stripped:
            if not set(stripped).issubset({"-", "+", " "}):
                current_file = stripped
                results.setdefault(current_file, [])
            continue
        # Separator line: compuesta solo de | - + y espacios
        if set(stripped).issubset({"|", "-", "+", " "}):
            continue
        # Header line: empieza con "function"
        if stripped.startswith("function"):
            continue
        # Data line
        parts = _split(line)
        if len(parts) >= 6 and current_file:
            try:
                results[current_file].append({
                    "function": parts[0],
                    "fields": int(parts[1]) if parts[1].isdigit() else 0,
                    "cyclo": int(parts[2]) if parts[2].isdigit() else 0,
                    "cognitive": int(parts[3]) if parts[3].isdigit() else 0,
                    "lines": int(parts[4]) if parts[4].isdigit() else 0,
                    "loc": int(parts[5]) if parts[5].isdigit() else 0,
                })
            except (ValueError, IndexError):
                logger.debug("línea de functions qlty no parseable, se omite: %s", stripped[:120])
    return results


# ── 3. Smells (batch SARIF) ────────────────────────────────────

def run_smells(paths: list[str], cwd: str | None = None) -> list[dict]:
    """Run qlty smells and return structured issues.

    Result pattern: el core @safe nunca lanza; ante fallo devuelve []
    y loggea el error (convention-001).
    """
    result = _run_smells_core(paths, cwd)
    if is_successful(result):
        return result.value_or([])
    error = result.failure()
    logger.error(
        "qlty smells fallo (%s: %s); sin smells",
        type(error).__name__, error,
    )
    return []


@safe
def _run_smells_core(paths: list[str], cwd: str | None) -> list[dict]:
    """Core puro de run_smells (puede lanzar; @safe captura)."""
    ensure_tools(["qlty"])
    r = subprocess.run(
        [QLTY_BIN, "smells", "--sarif"] + paths,
        capture_output=True, text=True, timeout=120, cwd=cwd,
    )
    if r.returncode != 0:
        raise RuntimeError(f"qlty smells exit {r.returncode}: {r.stderr[:200]}")
    return _parse_sarif(r.stdout)


def _parse_sarif(sarif_text: str) -> list[dict]:
    """Parsea output SARIF de qlty.

    Result pattern: el core @safe nunca lanza; ante JSON inválido
    devuelve [] y loggea el error (convention-001).
    """
    result = _parse_sarif_core(sarif_text)
    if is_successful(result):
        return result.value_or([])
    error = result.failure()
    logger.error(
        "Parse de SARIF fallo (%s: %s); sin findings",
        type(error).__name__, error,
    )
    return []


@safe
def _parse_sarif_core(sarif_text: str) -> list[dict]:
    """Core puro de _parse_sarif (puede lanzar)."""
    data = json.loads(sarif_text)
    issues = []
    for run in data.get("runs", []):
        for result in run.get("results", []):
            rule_id = result.get("ruleId", "")
            loc = result.get("locations", [{}])[0].get("physicalLocation", {})
            region = loc.get("region", {})
            artifact = loc.get("artifactLocation", {}).get("uri", "")
            issues.append({
                "id": rule_id,
                "severity": result.get("level", "note"),
                "file": artifact,
                "line": region.get("startLine", 0),
                "message": result.get("message", {}).get("text", ""),
            })
    return issues


# ── 4. Linter issues (batch SARIF) ─────────────────────────────

def run_check(
    paths: list[str], level: str = "medium", cwd: str | None = None
) -> list[dict]:
    """Run qlty check and return linter issues.

    qlty check returns exit code 1 when issues found (like a linter).
    We ignore exit code and parse SARIF output regardless.

    Result pattern: el core @safe nunca lanza; ante fallo devuelve []
    y loggea el error (convention-001).
    """
    result = _run_check_core(paths, level, cwd)
    if is_successful(result):
        return result.value_or([])
    error = result.failure()
    logger.error(
        "qlty check fallo (%s: %s); sin issues",
        type(error).__name__, error,
    )
    return []


@safe
def _run_check_core(paths: list[str], level: str, cwd: str | None) -> list[dict]:
    """Core puro de run_check (puede lanzar; @safe captura).

    Nota: exit code 1 = issues encontrados (como un linter) — se parsea
    el SARIF igualmente, no se trata como error.
    """
    ensure_tools(["qlty"])
    r = subprocess.run(
        [QLTY_BIN, "check", f"--level={level}", "--sarif"] + paths,
        capture_output=True, text=True, timeout=120, cwd=cwd,
    )
    return _parse_sarif(r.stdout)


# ── 5. Computed metrics (qlty + CPG) ───────────────────────────

def compute_file_metrics(filepath: str,
                         qlty_data: dict | None = None,
                         func_data: list[dict] | None = None,
                         graph: Graph | None = None) -> FileMetrics:
    """Compute metrics for a single file using qlty data + CPG."""
    cyclo_vals = [f["cyclo"] for f in (func_data or []) if f.get("cyclo")]
    cyclo_avg = sum(cyclo_vals) / len(cyclo_vals) if cyclo_vals else 0.0
    cyclo_max = max(cyclo_vals) if cyclo_vals else 0

    qlty = qlty_data or {}

    rating = "A"
    if cyclo_max > 20:
        rating = "D"
    elif cyclo_max > 10:
        rating = "C"
    elif cyclo_max > 5:
        rating = "B"

    coupling = 0
    if graph:
        coupling = sum(len(graph.get_outgoing(n.id))
                       for n in graph.nodes.values() if n.path == filepath)

    return FileMetrics(
        file=filepath,
        loc=qlty.get("loc", 0),
        functions=qlty.get("funcs", 0),
        classes=qlty.get("classes", 0),
        imports=0,
        cyclo_avg=cyclo_avg,
        cyclo_max=cyclo_max,
        coupling=coupling,
        complexity_rating=rating,
    )


def check_quality_gates(metrics: ProjectMetrics,
                        thresholds: dict[str, Any] | None = None) -> list[QualityGate]:
    if thresholds is None:
        thresholds = {}
    return [
        QualityGate(
            name="cyclomatic_complexity",
            actual=metrics.cyclo_max,
            threshold=thresholds.get("max_cyclo", 10),
            passed=metrics.cyclo_max <= thresholds.get("max_cyclo", 10),
        ),
        QualityGate(
            name="dead_code",
            actual=metrics.dead_code_count,
            threshold=thresholds.get("max_dead_code", 50),
            passed=metrics.dead_code_count <= thresholds.get("max_dead_code", 50),
        ),
        QualityGate(
            name="loc_per_file",
            actual=metrics.total_loc // max(metrics.total_files, 1),
            threshold=thresholds.get("max_loc_per_file", 500),
            passed=(metrics.total_loc // max(metrics.total_files, 1))
                   <= thresholds.get("max_loc_per_file", 500),
        ),
    ]
