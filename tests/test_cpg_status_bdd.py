"""tests/test_cpg_status_bdd.py — Status incluye estado CPG desde watcher.

SPEC-002: el comando status reporta cpg_status (up_to_date / stale) consultando
el watcher, que incluye verificación de fingerprint_match.
"""
from __future__ import annotations

import json
import os

import pytest

from orchestration.check_health_service import CheckHealthService


def test_status_incluye_cpg_status_desde_watcher(tmp_path):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    cognitive = project_root / ".cognitive"
    cognitive.mkdir()
    projects_dir = cognitive / "projects"
    projects_dir.mkdir()
    proj_dir = projects_dir / "test-proj"
    proj_dir.mkdir()
    data_dir = proj_dir / "data"
    data_dir.mkdir()

    (data_dir / "project.json").write_text(json.dumps({
        "project": {
            "id": "test-proj",
            "name": "Test",
            "location": {"repo_path": str(project_root)},
        }
    }))

    result = CheckHealthUseCase.execute("test-proj", str(project_root))

    assert "checks" in result
    assert "cpg" in result["checks"]
