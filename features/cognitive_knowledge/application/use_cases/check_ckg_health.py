"""use_cases/check_ckg_health.py — CheckCkgHealthUseCase.

Check CKG health: node count and status.
SSOT: solo cognitive_knowledge lee archivos CKG.
"""
from __future__ import annotations

import gzip
import json
import os

from returns.result import Result, safe


class CheckCkgHealthUseCase:
    """Check CKG health: node count and status.

    SRP: Single source of truth for CKG health data.
    """

    @staticmethod
    def execute(projects_data_dir: str) -> Result[dict, Exception]:
        """Check CKG status in the given data directory.

        Returns:
            Result[dict, Exception] con status y node_count
        """

        @safe
        def _read_ckg() -> dict:
            # Try .json.gz first (canonical), then .json (legacy)
            ckg_path = os.path.join(projects_data_dir, "ckg.json.gz")
            data = _read_ckg_file(ckg_path)
            if data is not None:
                total = len(data.get("nodes", data.get("docs", [])))
                return {"status": f"{total} nodes", "node_count": total}

            ckg_old = os.path.join(projects_data_dir, "ckg.json")
            old_data = _read_ckg_file(ckg_old)
            if old_data is not None:
                total = len(old_data.get("nodes", old_data.get("docs", [])))
                return {"status": f"{total} nodes", "node_count": total}

            return {"status": "empty", "node_count": 0}

        return _read_ckg()


def _read_ckg_file(path: str) -> dict | None:
    """Read a CKG file (.json or .json.gz). Returns None on error/missing."""
    if not os.path.exists(path):
        return None
    try:
        if path.endswith(".gz"):
            with gzip.open(path) as f:
                return json.load(f)
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None
