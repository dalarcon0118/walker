"""use_cases/check_cpg_health.py — CheckCpgHealthUseCase.

Check CPG health: node count and status.
SSOT: solo code_analysis lee archivos CPG.
"""
from __future__ import annotations

import gzip
import json
import logging
import os

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)


class CheckCpgHealthUseCase:
    """Check CPG health: node count and status.

    SRP: Single source of truth for CPG health data.
         No other feature module should read CPG files directly.
    """

    @staticmethod
    def execute(projects_data_dir: str) -> dict:
        """Check CPG status in the given data directory.

        Args:
            projects_data_dir: Path to .cognitive/projects/<pid>/data/

        Returns:
            dict with keys: status (str), node_count (int)
        """
        # Try .json.gz first (canonical), then .json (legacy)
        cpg_path = os.path.join(projects_data_dir, "cpg.json.gz")
        data = _read_cpg_file(cpg_path)
        if data is not None:
            total = len(data.get("nodes", data.get("docs", [])))
            return {"status": f"{total} nodes", "node_count": total}

        cpg_old = os.path.join(projects_data_dir, "cpg.json")
        old_data = _read_cpg_file(cpg_old)
        if old_data is not None:
            total = len(old_data.get("nodes", old_data.get("docs", [])))
            return {"status": f"{total} nodes", "node_count": total}

        return {"status": "not scanned", "node_count": 0}


def _read_cpg_file(path: str) -> dict | None:
    """Read a CPG file (.json or .json.gz).

    Result pattern: el core @safe nunca lanza; ante fallo devuelve None
    y loggea el error (convention-001).
    """
    if not os.path.exists(path):
        return None
    result = _read_cpg_file_core(path)
    if is_successful(result):
        return result.value_or(None)
    error = result.failure()
    logger.warning(
        "No se pudo leer CPG %s (%s: %s)", path, type(error).__name__, error,
    )
    return None


@safe
def _read_cpg_file_core(path: str) -> dict:
    """Core puro de lectura de CPG (puede lanzar)."""
    if path.endswith(".gz"):
        with gzip.open(path) as f:
            return json.load(f)
    with open(path) as f:
        return json.load(f)
