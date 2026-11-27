"""BDD: plan_sprint idempotente + spec_task refinamiento (SPRINT planning seguro)."""
from __future__ import annotations

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext
from fast_mcp.features.tasks.handlers.sprint_planning import _build_handlers


class FakeService:
    def __init__(self) -> None:
        self.springs: dict[str, dict] = {}
        self.specs: dict[str, list[dict]] = {}
        self.sessions: dict[str, dict] = {}

    def create_spring(self, spring_id, goal="", status="planning", project_id="", session_id="", context=None, **kw):
        spring = {
            "spring_id": spring_id,
            "goal": goal,
            "status": status,
            "project_id": project_id,
            "session_id": session_id,
            "context": context if context is not None else {},
        }
        self.springs[spring_id] = spring
        return dict(spring)

    def get_spring(self, spring_id):
        s = self.springs.get(spring_id)
        return dict(s) if s else None

    def update_spring(self, spring_id, data):
        s = self.springs.get(spring_id)
        if not s:
            return None
        for k, v in (data or {}).items():
            if v is not None:
                s[k] = v
        return dict(s)

    def open_session(self, session_id, **kw):
        self.sessions.setdefault(session_id, {"session_id": session_id})
        self.sessions[session_id].update({k: v for k, v in kw.items() if v is not None})
        return dict(self.sessions[session_id])

    def get_session(self, session_id):
        s = self.sessions.get(session_id)
        return dict(s) if s else None

    def get_specs(self, spring_id, status=""):
        specs = [dict(s) for s in self.specs.get(spring_id, [])]
        if status:
            specs = [s for s in specs if s.get("status") == status]
        return specs

    def get_spec(self, spring_id, spec_id):
        for s in self.specs.get(spring_id, []):
            if s.get("_id") == spec_id or s.get("spec_id") == spec_id:
                return dict(s)
        return None

    def add_spec(self, spring_id, objective, domain="", spec_type="feature", spec_index=None,
                 result_state="", files=None, status="draft", **kw):
        specs = self.specs.setdefault(spring_id, [])
        if spec_index is None:
            used = [s.get("spec_index", 0) for s in specs]
            taken = {s.get("_id") for s in specs}
            spec_index = (max(used) + 1) if used else 0
            while f"SPEC-{spec_index + 1:03d}" in taken:
                spec_index += 1
        spec_id = f"SPEC-{spec_index + 1:03d}"
        spec = {
            "_id": spec_id,
            "spec_id": spec_id,
            "spring_id": spring_id,
            "objective": objective,
            "domain": domain,
            "spec_type": spec_type,
            "spec_index": spec_index,
            "result_state": result_state,
            "files": files or [],
            "status": status,
            "acceptance_criteria": [],
            "predicates": {},
            "task_context": {},
        }
        specs.append(spec)
        return dict(spec)

    def update_spec(self, spring_id, spec_id, updates):
        for s in self.specs.get(spring_id, []):
            if s.get("_id") == spec_id or s.get("spec_id") == spec_id:
                for k, v in (updates or {}).items():
                    if v is not None:
                        s[k] = v
                return dict(s)
        return None

    def get_active_spring(self):
        return None

    def set_spring_status(self, spring_id, status):
        if spring_id in self.springs:
            self.springs[spring_id]["status"] = status


def _ctx():
    return ExecutionContext(chain_dir="/tmp/proj", session_id="s1")


def test_plan_sprint_existing_preserves_context_and_status():
    svc = FakeService()
    svc.create_spring(
        "SPR-1", goal="old", status="active", project_id="p", session_id="s1",
        context={"session_ids": ["roadmap-session-1"], "foo": "bar"},
    )
    svc.specs["SPR-1"] = [{"_id": "SPEC-001", "spec_id": "SPEC-001", "objective": "x"}]

    plan, *_ = _build_handlers(svc)
    result = plan(_ctx(), {
        "spring_id": "SPR-1",
        "goal": "new goal",
        "session_id": "s2",
        "tasks": [],
    })

    assert result.ok is True
    spring = svc.springs["SPR-1"]
    assert spring["status"] == "active"
    assert spring["goal"] == "new goal"
    assert "roadmap-session-1" in spring["context"]["session_ids"]
    assert "s2" in spring["context"]["session_ids"]
    assert spring["context"].get("foo") == "bar"
    assert len(svc.specs["SPR-1"]) == 1


def test_plan_sprint_preserva_campos_de_specs_existentes_por_indice():
    """Re-planear no borra predicados/depends_on/acceptance ya escritos.

    El workflow ejecuta `plan_sprint` en el submit y otra vez en `advance`;
    recrear la spec vacía hacía que el segundo paso perdiera los campos que
    agregó `spec_predicates`/`spec_update` (medido 2026-11-24).
    """
    svc = FakeService()
    svc.create_spring("SPR-1", goal="g", status="planning")
    svc.specs["SPR-1"] = [{
        "_id": "SPEC-001", "spec_id": "SPEC-001", "spring_id": "SPR-1",
        "objective": "viejo", "status": "draft", "spec_index": 0,
        "acceptance_criteria": ["ac"],
        "predicates": {"items": [{"type": "command_exit", "command": "pytest"}]},
        "task_context": {"k": "v"},
        "depends_on": ["SPEC-000"],
    }]

    plan, *_ = _build_handlers(svc)
    out = plan(_ctx(), {
        "spring_id": "SPR-1",
        "goal": "g",
        "tasks": [{"objective": "nuevo", "type": "feature", "domain": "d"}],
    })

    assert out.ok is True
    spec = svc.get_spec("SPR-1", "SPEC-001")
    assert spec["objective"] == "nuevo"
    assert spec["acceptance_criteria"] == ["ac"]
    assert spec["predicates"]["items"]
    assert spec["depends_on"] == ["SPEC-000"]
    assert spec["task_context"] == {"k": "v"}
    assert len(svc.specs["SPR-1"]) == 1


def test_spec_task_refine_same_spec():
    svc = FakeService()
    svc.create_spring("SPR-1", goal="g", status="planning")
    svc.specs["SPR-1"] = [{
        "_id": "SPEC-002", "spec_id": "SPEC-002", "spring_id": "SPR-1",
        "objective": "old", "status": "draft", "acceptance_criteria": [],
        "predicates": {}, "task_context": {"k": "v"}, "spec_index": 1,
    }]

    _, _, spec_task, _ = _build_handlers(svc)
    result = spec_task(_ctx(), {
        "spring_id": "SPR-1",
        "spec_id": "SPEC-002",
        "acceptance_criteria": ["ac1"],
    })

    assert result.ok is True
    assert result.data["spec_id"] == "SPEC-002"
    assert result.data["updated"] is True
    spec = svc.get_spec("SPR-1", "SPEC-002")
    assert spec["task_context"] == {"k": "v"}
    assert spec["acceptance_criteria"] == ["ac1"]
    assert len(svc.specs["SPR-1"]) == 1


def test_spec_task_refine_devuelve_la_spec_como_task_id():
    """Especificar SPEC-002 debe mover el task_id del workflow a SPEC-002.

    El `task_id` del `exec_data` es el de la tarea anterior; devolverlo hacía
    que `transform_spec` escribiera siempre sobre la primera spec.
    """
    svc = FakeService()
    svc.create_spring("SPR-1", goal="g", status="planning")
    svc.specs["SPR-1"] = [{
        "_id": "SPEC-002", "spec_id": "SPEC-002", "spring_id": "SPR-1",
        "objective": "old", "status": "draft", "spec_index": 1,
        "acceptance_criteria": [], "predicates": {}, "task_context": {},
    }]

    _, _, spec_task, _ = _build_handlers(svc)
    result = spec_task(_ctx(), {
        "spring_id": "SPR-1",
        "spec_id": "SPEC-002",
        "task_id": "SPEC-001",
        "acceptance_criteria": ["ac"],
    })

    assert result.ok is True
    assert result.data["task_id"] == "SPEC-002"


def test_spec_task_ignora_el_status_pending_del_engine():
    """`status=pending` lo siembra el motor al entrar al sub-estado."""
    svc = FakeService()
    svc.create_spring("SPR-1", goal="g", status="planning")
    svc.specs["SPR-1"] = [{
        "_id": "SPEC-002", "spec_id": "SPEC-002", "spring_id": "SPR-1",
        "objective": "old", "status": "draft", "spec_index": 1,
        "acceptance_criteria": [], "predicates": {}, "task_context": {},
    }]

    _, _, spec_task, _ = _build_handlers(svc)
    result = spec_task(_ctx(), {
        "spring_id": "SPR-1",
        "spec_id": "SPEC-002",
        "status": "pending",
        "acceptance_criteria": ["ac"],
    })

    assert result.ok is True
    assert svc.get_spec("SPR-1", "SPEC-002")["status"] == "draft"


def test_spec_task_refine_nonexistent_fails():
    svc = FakeService()
    svc.create_spring("SPR-1", goal="g", status="planning")

    _, _, spec_task, _ = _build_handlers(svc)
    result = spec_task(_ctx(), {
        "spring_id": "SPR-1",
        "spec_id": "SPEC-999",
        "objective": "x",
    })

    assert result.ok is False


def test_spec_task_without_spec_id_adds():
    svc = FakeService()
    svc.create_spring("SPR-1", goal="g", status="planning")

    _, _, spec_task, _ = _build_handlers(svc)
    before = len(svc.specs.get("SPR-1", []))
    result = spec_task(_ctx(), {
        "spring_id": "SPR-1",
        "objective": "new spec",
    })

    assert result.ok is True
    assert result.data["updated"] is False
    assert len(svc.specs["SPR-1"]) == before + 1


def test_validate_plan_acceptance_missing():
    svc = FakeService()
    svc.create_spring("SPR-1", goal="g", status="planning")
    svc.specs["SPR-1"] = [{
        "_id": "SPEC-001", "spec_id": "SPEC-001", "spring_id": "SPR-1",
        "objective": "obj", "status": "draft", "acceptance_criteria": [],
        "predicates": {}, "spec_index": 0,
    }]

    _, validate_plan, _, _ = _build_handlers(svc)
    result = validate_plan(_ctx(), {"spring_id": "SPR-1"})

    assert result.ok is True
    assert result.data["valid"] is False
    assert result.data["acceptance_missing"]  # non-empty
