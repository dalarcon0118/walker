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

from core.engine.domain.session import SessionStore


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
    from shared.core.path_resolver import get_walker_root

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


@pytest.fixture(autouse=True)
def _sandbox_composition_singleton():
    """Resetea el singleton de AppContainer entre tests."""
    from features.main.container import AppContainer

    AppContainer.reset()
    yield
    AppContainer.reset()


@pytest.fixture(autouse=True)
def _populate_service_locator(_sandbox_composition_singleton):
    """Puebla el service locator neutro de core/engine con el composition root.

    ADR-045: los callers internos de core/engine (trace_log, query_manager,
    repos de features) resuelven cognitive_graph/cognitive_searcher/workflow
    por get_module_service(). El composition root (AppContainer.build)
    registra TODOS los servicios DI (libs de shared/ + task_registry +
    side_effect_engine + workflow) y set_service_locator() los expone al
    facade neutro de core/engine. Así la suite no depende del orden de tests.
    """
    from features.main.container import AppContainer

    AppContainer.build()
    yield
    from core.engine.bootstrap import reset_module_registry

    reset_module_registry()


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
    from features.main.cli_interface.mcp_adapter import _handle_request

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
    """Registra handlers mock para las actions del flujo E2E artificial.

    El flujo de test (test_workflow_e2e_real_bdd.py / test_agent_e2e_bdd.py)
    escribe su PROPIO workflow.v3 con actions artificiales (gather_design,
    user_gate, implement, scan_code, qa_run, ...) que no mapean 1:1 a
    handlers reales con contratos compatibles (spec_task real valida 7
    campos; review_code real requiere port CPG). Por eso los mocks PARCHAN
    el registry (pueden pisar tasks reales): el test valida el ENGINE
    (auto-advance, side effects, delta) con handlers deterministas.

    Composition root real (capa_rout.md D1): el registry DEBE venir de
    bootstrap()/bootstrap_tasks(). Si bootstrap() no puebla las tasks
    esperadas, el test de completitud (test_registry_completeness) y el
    test de regresión del camino real (test_workflow_production_di) fallan
    — la rotura de composición es visible en CI, no enmascarada aquí.
    """
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
    """Registra los side effects reales del flujo E2E (SSOT de main,
    SPEC-014 Fase 4: nombres canónicos sin alias legacy)."""
    from features.main.task_execution.side_effects.decision import create_adr
    from features.main.task_execution.side_effects.planning import advance_sprint_task
    from features.main.task_execution.side_effects.spec_transforms import transform_spec

    engine.register("transform_spec", transform_spec)
    engine.register("create_adr", create_adr)
    engine.register("advance_sprint_task", advance_sprint_task)


def make_orchestration_service(real: bool = True, **overrides):
    """Construye un OrchestrationService con composition root válido (Gap 1).

    El constructor de OrchestrationService ahora EXIGE task_registry y
    side_effect_engine (composition root obligatorio, capa_rout.md D1) — no
    hay defaults que fabriquen registries vacíos. Este helper ofrece los dos
    caminos documentados:

      real=True (Camino B): bootstrap() produce los registries poblados —
      para tests que ejercitan submit/transition con tasks reales.
      real=False (Camino A): registries mínimos explícitos (InMemoryTaskRegistry
      + SideEffectEngine vacíos) — para tests que solo prueban prompts o
      side effects y declaran que NO necesitan tasks.

    Los overrides permiten inyectar repos fakes (tests de bajo nivel).
    """
    from core.engine.application.orchestrator import OrchestrationService

    kwargs = dict(overrides)
    if "task_registry" not in kwargs or "side_effect_engine" not in kwargs:
        if real:
            from features.main.container import AppContainer

            app = AppContainer.build()
            registry = app.task_registry
            se = app.side_effect_engine
        else:
            from core.engine.infrastructure.task_registry import InMemoryTaskRegistry
            from core.engine.infrastructure.side_effects import SideEffectEngine

            registry = InMemoryTaskRegistry()
            se = SideEffectEngine()
        kwargs.setdefault("task_registry", registry)
        kwargs.setdefault("side_effect_engine", se)
    return OrchestrationService(**kwargs)


def make_project_with_active_sprint(tmp_path, project_id: str = "walker", sprint_id: str = "sprint-cli"):
    """Monta un proyecto COMPLETO (cognitivo + spring activo + workflow state)
    y lo registra en el registro global (reversible).

    Uso para tests CLI/MCP que dependen de un sprint activo para resolver el
    chain_dir (find_active_sprint) — el estado real del repo no debe ser
    precondición de un test (test_get_status_resuelve_desde_cwd,
    test_cli_walker_kg_diagnose_identifies_gap).

    Retorna: (tmp_path_str, sprint_dir_str). El registro global queda
    restaurado al final del test (context manager).
    """
    from features.delta_tracking import SpringService

    cog = Path(tmp_path) / ".cognitive" / "projects" / project_id
    cog.mkdir(parents=True)
    (cog / "data").mkdir()
    (cog / "data" / "project.json").write_text(json.dumps({
        "project_id": project_id,
        "name": project_id,
        "repo_path": str(tmp_path),
        "language": "python",
        "framework": "unknown",
        "status": "active",
    }))
    from features.delta_tracking.infrastructure.cognitive_delta_repo import (
        CognitiveDeltaRepository,
    )

    CognitiveDeltaRepository(str(cog))
    svc = SpringService(str(cog))
    svc.create_spring(
        sprint_id, goal="CLI sprint", status="active",
        project_id=project_id, session_id="sess-cli",
    )
    sprint_dir = cog / "data" / "spring" / sprint_id
    sprint_dir.mkdir(parents=True, exist_ok=True)
    from core.engine.infrastructure.repositories import WorkflowStateRepository
    from core.engine.domain.entities import WorkflowState

    wf = WorkflowState(current_node="SPRINT", current_sub_state="PLANNING")
    wf.context["chain_dir"] = str(sprint_dir)
    wf.context["project_id"] = project_id
    wf.context["session_id"] = "sess-cli"
    WorkflowStateRepository().save(str(sprint_dir), wf)

    reg_ctx = register_project_in_global_registry(str(tmp_path), project_id).__enter__()
    return str(tmp_path), str(sprint_dir), reg_ctx


def register_project_in_global_registry(tmp_path: str, project_id: str = "walker", monkeypatch=None) -> None:
    """Registra un proyecto temporal en el registro global data/projects.json.

    REVERSIBLE: guarda el estado original y restaura al final del test (teardown
    vía monkeypatch o manual). Los tests de smoke (MCP submit/transition) escriben
    este registro para que find_active_sprint/resolve_paths resuelvan el proyecto
    temporal — pero NO deben contaminar el registro real entre corridas.

    Uso:
        with register_project_in_global_registry(tmp_path):
            ...  # el registro queda modificado dentro del bloque
        # fuera del bloque: restaurado
    """
    from shared.core.path_resolver import get_walker_root

    reg_path = get_walker_root() / "data" / "projects.json"
    original = reg_path.read_bytes() if reg_path.is_file() else None

    data = {"projects": {}}
    if original:
        try:
            data = json.loads(original)
        except (json.JSONDecodeError, OSError):
            data = {"projects": {}}
    data["projects"][project_id] = {
        "project_id": project_id,
        "name": project_id,
        "repo_path": tmp_path,
        "active": True,
    }
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    reg_path.write_text(json.dumps(data, indent=2))

    class _Restorer:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            if original is not None:
                reg_path.write_bytes(original)
            else:
                reg_path.unlink(missing_ok=True)
            return False

    return _Restorer()