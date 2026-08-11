"""Fixtures globales de la suite de tests.

Seguridad: fuerza SessionStore.SESSIONS_DIR a un directorio temporal por
test para que NINGUNA prueba toque el <cognitive>/sessions/ real.

Motivación (incidente real): cuando un test ejercita la cadena pública
(use_cases -> WorkflowAPI.ensure_session -> SessionStore.gc_stale) con
SESSIONS_DIR en None, la resolución via config apunta al dir real del
proyecto y un GC de sesiones stale puede borrar datos productivos.
Este fixture hace que el default de toda la suite sea un tamptdir aislado.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from features.engine.domain.session import SessionStore


@pytest.fixture(autouse=True)
def _sandbox_sessions_dir(tmp_path, monkeypatch):
    """Apunta SessionStore a un sessions_dir temporal por cada test."""
    sdir = tmp_path / "walkertest-sessions"
    sdir.mkdir(exist_ok=True)
    monkeypatch.setattr(SessionStore, "SESSIONS_DIR", str(sdir))
    monkeypatch.setattr(SessionStore, "ACTIVE_FILE", None)
    monkeypatch.setattr(SessionStore, "_LAST_GC", 0.0)
    yield
    # Restaura el estado de clase para no filtrar tmp_path entre tests.
    SessionStore.SESSIONS_DIR = None
    SessionStore.ACTIVE_FILE = None
    SessionStore._LAST_GC = 0.0


@pytest.fixture(autouse=True)
def _sandbox_global_registry(tmp_path, monkeypatch):
    """Aísla el REGISTRO GLOBAL de proyectos (data/projects.json).

    data/projects.json (raíz del walker) es el índice de TODOS los proyectos
    que el walker administra — distinto del MANIFIESTO de un proyecto
    (.cognitive/{pid}/data/project.json). NO confundir.

    Motivación (incidente real): cuando un test ejercita la cadena pública
    (ProjectService.resolve_* → auto-register) con repo_paths temporales o
    de otra sesión, reescribe data/projects.json a mitad de suite y rompe los
    tests CLI/registry que dependen de su estado nativo (flaky por orden).

    Backup + restore por test: cada test parte del estado nativo del registro,
    sin importar qué mutaron los tests anteriores.
    """
    from libs.path_resolver import get_walker_root

    reg_path = get_walker_root() / "data" / "projects.json"
    snapshot = None
    if reg_path.is_file():
        snapshot = reg_path.read_bytes()
    yield
    if snapshot is not None:
        reg_path.parent.mkdir(parents=True, exist_ok=True)
        reg_path.write_bytes(snapshot)
    else:
        # El archivo no existía al inicio: si un test lo creó, lo eliminamos.
        reg_path.unlink(missing_ok=True)


@pytest.fixture
def mcp_project(tmp_path):
    """Crea un proyecto temporal con .cognitive/ listo para tests MCP."""
    cog = tmp_path / ".cognitive" / "projects" / "walker"
    cog.mkdir(parents=True)
    (cog / "data").mkdir()
    (cog / "data" / "spring").mkdir()
    project_json = {
        "project_id": "walker",
        "name": "walker",
        "repo_path": str(tmp_path),
        "language": "python",
        "framework": "unknown",
        "status": "active",
    }
    with open(cog / "data" / "project.json", "w") as f:
        json.dump(project_json, f)
    from features.delta_tracking.infrastructure.cognitive_delta_repo import (
        CognitiveDeltaRepository,
    )
    CognitiveDeltaRepository(str(cog))
    return str(cog)


def call_mcp_tool(project_root: str, tool_name: str, arguments: dict) -> dict:
    """Helper reutilizable: invoca una tool MCP via JSON-RPC y devuelve el payload."""
    from features.cli_interface.mcp_adapter import _handle_request

    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    resp = _handle_request(msg)
    content = resp.get("result", {}).get("content", [])
    text = content[0].get("text", "") if content else ""
    try:
        return json.loads(text) if text.startswith("{") else {"raw": text}
    except json.JSONDecodeError:
        return {"raw": text}


# ── Builders de setup compartidos (SPEC-003 review_architecture) ───────
# Familias de layout que estaban duplicadas en cada archivo de test:
#   - make_walker_project: proyecto walker cognitivo (.cognitive/projects/walker)
#   - make_chain_dir: chain_dir mínimo (data/project.json)
#   - make_v2_project: layout v2 legacy (project.meta/identity/location)
# Cada archivo de test conserva un wrapper de una línea con su firma exacta.

def make_walker_project(tmp_path) -> str:
    """Crea el proyecto walker cognitivo completo + repo delta. Retorna str(cog)."""
    cog = Path(tmp_path) / ".cognitive" / "projects" / "walker"
    cog.mkdir(parents=True)
    (cog / "data").mkdir()
    project_json = {
        "project_id": "walker",
        "name": "walker",
        "repo_path": str(tmp_path),
        "language": "python",
        "framework": "unknown",
        "status": "active",
    }
    with open(cog / "data" / "project.json", "w") as f:
        json.dump(project_json, f)
    from features.delta_tracking.infrastructure.cognitive_delta_repo import (
        CognitiveDeltaRepository,
    )

    CognitiveDeltaRepository(str(cog))
    return str(cog)


def make_chain_dir(tmp_path, name: str = "sprint", project_id: str = "walker", project_name: str = "") -> str:
    """Crea un chain_dir mínimo con data/project.json. Retorna str(chain_dir)."""
    chain_dir = str(Path(tmp_path) / name)
    os.makedirs(chain_dir, exist_ok=True)
    os.makedirs(os.path.join(chain_dir, "data"), exist_ok=True)
    manifest = {"project_id": project_id}
    if project_name:
        manifest["name"] = project_name
    with open(os.path.join(chain_dir, "data", "project.json"), "w") as f:
        json.dump(manifest, f)
    return chain_dir


def make_v2_project(tmpdir: str) -> tuple[str, str]:
    """Crea el layout v2 legacy (project.meta/identity/location). Retorna (chain_dir, project_root)."""
    project_root = os.path.join(tmpdir, "proj")
    chain_dir = os.path.join(project_root, "data", "spring", "test-sprint")
    os.makedirs(os.path.join(project_root, "data"), exist_ok=True)
    os.makedirs(chain_dir, exist_ok=True)
    project_json = {
        "project": {
            "meta": {"project_id": "test-project", "version": "2.0"},
            "identity": {"name": "test-project"},
            "location": {"repo_path": project_root},
        }
    }
    with open(os.path.join(project_root, "data", "project.json"), "w") as f:
        json.dump(project_json, f, ensure_ascii=False)
    return chain_dir, project_root


def register_mock_tasks(registry) -> None:
    """Registra handlers mock para todos los actions del flujo E2E."""
    mock_tasks = {
        "plan_sprint": lambda ctx, data: {"status": "planned", "sprint_id": "sprint-e2e"},
        "spec_task": lambda ctx, data: {"task_id": "SPEC-001", "status": "specified"},
        "review_task": lambda ctx, data: {"decision": "approve"},
        "gather_design": lambda ctx, data: {"design_done": True},
        "gather_analysis": lambda ctx, data: {"analysis_done": True},
        "user_gate": lambda ctx, data: {"approved": True},
        "implement": lambda ctx, data: {"code_complete": True},
        "scan_code": lambda ctx, data: {"scan_complete": True},
        "review_code": lambda ctx, data: {"review_passed": True},
        "report_gate": lambda ctx, data: {"report_approved": True},
        "verify_user_gate": lambda ctx, data: {"approved": True},
        "qa_run": lambda ctx, data: {"qa_passed": True},
        "po_accept": lambda ctx, data: {"po_approved": True},
    }
    for action, handler in mock_tasks.items():
        registry.register(action, handler)


def register_side_effects(engine) -> None:
    """Registra los side effects reales del flujo E2E (transform_spec, create_adr_from_spec)."""
    from features.task_execution.side_effects.create_adr import create_adr_from_spec
    from features.task_execution.side_effects.spec_transform import transform_spec

    engine.register("transform_spec", transform_spec)
    engine.register("create_adr_from_spec", create_adr_from_spec)