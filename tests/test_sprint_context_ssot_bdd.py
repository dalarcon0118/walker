"""test_sprint_context_ssot_bdd.py — un solo sprint para todas las superficies.

Regresión medida 2026-11-24 en `client-chat`:

    status           → SPR-Se542f712 (activo)
    cockpit          → ━━━ (sin sprint) ━━━
    workflow_state   → Goal: (vacío), Tasks: 0/0

Causa: cada superficie resolvía el sprint con su propia política y
`workflow_state` lo derivaba del basename del `chain_dir`. ADR-WALKER-085
establece un resolver único (sesión > activo > planning).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from returns.result import Success

import fast_mcp.composition as composition
import fast_mcp.memories.handlers.knowledge as knowledge
from fast_mcp.features.workflow.application import prompt_context as pc
from fast_mcp.features.delta_tracking.application.services.sprint_context import (
    resolve_sprint_context,
)
from fast_mcp.features.delta_tracking.application.services.sprint_service import (
    SprintService,
)
from fast_mcp.features.delta_tracking.infrastructure.cognitive_delta_repo import (
    CognitiveDeltaRepository,
)
from fast_mcp.memories.handlers.knowledge import handle_brief
from fast_mcp.shared_kernel.cognitive_graph import build_cognitive_graph


class _FakeSprintService:
    """Doble mínimo con la misma superficie que consume el resolver."""

    def __init__(self, springs, sessions=None, specs=None):
        self._springs = {s["spring_id"]: s for s in springs}
        self._sessions = sessions or {}
        self._specs = specs or {}

    def get_spring(self, spring_id):
        return self._springs.get(spring_id)

    def get_session(self, session_id):
        return self._sessions.get(session_id)

    def get_session_plan(self, session_id):
        return (self._sessions.get(session_id) or {}).get("flight_plan")

    def get_active_spring(self):
        active = [s for s in self._springs.values() if s.get("status") == "active"]
        active.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        return active[0] if active else None

    def list_springs(self, status=""):
        return [
            s for s in self._springs.values()
            if not status or s.get("status") == status
        ]

    def get_specs(self, spring_id, status=""):
        return [
            s for s in self._specs.get(spring_id, [])
            if not status or s.get("status") == status
        ]


def _spring(**over):
    base = {
        "spring_id": "SPR-REAL",
        "goal": "goal real",
        "status": "active",
        "session_id": "",
        "context": {},
        "updated_at": "2026-11-24T00:00:00+00:00",
    }
    base.update(over)
    return base


def _spec(spec_id="SPEC-001", status="in_progress"):
    return {"spec_id": spec_id, "objective": "objetivo", "status": status,
            "files": ["a.py"], "acceptance_criteria": []}


# ── Política única ──────────────────────────────────────────────────────

def test_resolver_prefiere_el_sprint_de_la_sesion_sobre_el_activo():
    fake = _FakeSprintService(
        springs=[
            _spring(spring_id="SPR-OTRO", session_id="otra"),
            _spring(spring_id="SPR-SESION", session_id="ses-1"),
        ],
        sessions={"ses-1": {"spring_id": "SPR-SESION"}},
        specs={"SPR-SESION": [_spec()]},
    )

    ctx = resolve_sprint_context(fake, "client-chat", session_id="ses-1")

    assert ctx.spring_id == "SPR-SESION"
    assert ctx.resolved_by == "session"
    assert ctx.session_linked is True
    assert [s["spec_id"] for s in ctx.specs] == ["SPEC-001"]


def test_resolver_cae_al_activo_cuando_la_sesion_no_declara_sprint():
    fake = _FakeSprintService(
        springs=[_spring(spring_id="SPR-ACTIVO", session_id="otra")],
        sessions={"ses-1": {"spring_id": ""}},
        specs={"SPR-ACTIVO": [_spec()]},
    )

    ctx = resolve_sprint_context(fake, "client-chat", session_id="ses-1")

    assert ctx.spring_id == "SPR-ACTIVO"
    assert ctx.resolved_by == "active"
    assert ctx.session_linked is False
    assert "session_not_linked" in ctx.diagnostics


def test_resolver_no_deriva_el_sprint_del_basename_del_chain_dir():
    fake = _FakeSprintService(springs=[_spring(spring_id="SPR-REAL")])

    ctx = resolve_sprint_context(fake, "/proyectos/client-chat")

    assert ctx.spring_id == "SPR-REAL"
    assert ctx.spring_id != "client-chat"


def test_resolver_reporta_sprint_activo_sin_atribucion():
    """ADR-077: un activo sin sesión dueña es una anomalía, no un estado sano."""
    fake = _FakeSprintService(springs=[_spring(spring_id="SPR-HUERFANO")])

    ctx = resolve_sprint_context(fake, "/proyectos/client-chat")

    assert ctx.spring_id == "SPR-HUERFANO"
    assert "sprint_unattributed" in ctx.diagnostics


def test_resolver_reporta_puntero_de_sesion_a_sprint_inexistente():
    fake = _FakeSprintService(
        springs=[_spring(spring_id="SPR-OTRO")],
        sessions={"ses-1": {"spring_id": "SPR-FANTASMA"}},
    )

    ctx = resolve_sprint_context(fake, "/proyectos/client-chat", session_id="ses-1")

    assert "session_points_to_missing_spring:SPR-FANTASMA" in ctx.diagnostics
    assert ctx.resolved_by == "active"


# ── Servicio: get_spring_id / load_tasks ────────────────────────────────

def _real_service(tmp_path):
    graph = build_cognitive_graph(str(tmp_path))
    graph.register_index("delta", {
        "fields": {"task": "spec_id", "spec": "spec_id", "spring": "spring_id",
                   "domain": "domain", "type": "type"},
        "types": {"spec": {}, "spring": {}},
        "description": "d",
        "search": {"type": "fulltext", "fields": ["objective", "title", "description"]},
    })
    return SprintService(project_root=str(tmp_path), repo=CognitiveDeltaRepository(graph))


def test_get_spring_id_devuelve_el_sprint_real_no_el_basename(tmp_path):
    svc = _real_service(tmp_path)
    svc.create_spring("SPR-REAL", goal="g", status="active", project_id="p")
    chain = str(tmp_path / "client-chat")

    assert svc.get_spring_id(chain) == "SPR-REAL"


def test_get_active_spring_atribuye_por_context_session_ids(tmp_path, caplog):
    """Un activo con `session_id=""` pero `context.session_ids` NO es huérfano."""
    svc = _real_service(tmp_path)
    svc.create_spring(
        "SPR-CTX",
        goal="g",
        status="active",
        project_id="p",
        session_id="",
        context={"session_ids": ["ses-1"]},
    )

    with caplog.at_level(logging.WARNING):
        active = svc.get_active_spring()

    assert active["spring_id"] == "SPR-CTX"
    assert not any("sin atribución" in r.message for r in caplog.records)


def test_load_tasks_usa_el_sprint_vigente_y_no_el_basename(tmp_path):
    svc = _real_service(tmp_path)
    svc.create_spring("SPR-REAL", goal="g", status="active", project_id="p")
    svc.add_spec("SPR-REAL", objective="Spec A")
    chain = str(tmp_path / "client-chat")

    tasks = svc.load_tasks(chain)

    assert [t["objective"] for t in tasks] == ["Spec A"]


# ── Prompt del workflow ─────────────────────────────────────────────────

def test_prompt_extra_usa_el_sprint_de_la_sesion(monkeypatch):
    fake = _FakeSprintService(
        springs=[_spring(spring_id="SPR-REAL", goal="goal real", session_id="ses-1")],
        sessions={"ses-1": {"spring_id": "SPR-REAL"}},
        specs={"SPR-REAL": [_spec(status="completed"), _spec("SPEC-002", "in_progress")]},
    )
    monkeypatch.setattr(composition, "build_sprint_service", lambda _d: fake)
    state = SimpleNamespace(
        current_node="SPRINT",
        task_id="T-1",
        context={"task_id": "T-1"},
        node_executions=[],
        sub_state_data={},
    )

    extra = pc.build_prompt_extra(state, "/proyectos/client-chat", "ses-1")

    assert extra["sprint_id"] == "SPR-REAL"
    assert extra["goal_display"] == "Goal: goal real"
    assert extra["task_count"] == 2
    assert extra["completed_count"] == 1


def test_brief_publica_el_mismo_sprint_que_el_resolver(monkeypatch):
    fake = _FakeSprintService(
        springs=[_spring(spring_id="SPR-REAL", session_id="ses-1")],
        sessions={"ses-1": {"spring_id": "SPR-REAL", "flight_plan": {
            "task": "T-1", "scope": ["a.py"], "next": "verificar",
        }}},
        specs={"SPR-REAL": [_spec()]},
    )
    monkeypatch.setattr(composition, "build_sprint_service", lambda _d: fake)
    monkeypatch.setattr(knowledge, "_resolve_anchor",
                        lambda *_: ({"type": "file", "value": "a.py", "resolved": True},
                                    ["a.py"], None))
    monkeypatch.setattr(knowledge, "handle_preflight",
                        lambda _ctx, **_: {"files": [], "tests": [], "unmapped": []})
    from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import (
        CkgReadService,
    )
    monkeypatch.setattr(CkgReadService, "gather_context",
                        staticmethod(lambda *_a, **_k: Success([])))
    monkeypatch.setattr(composition, "workflow_tool_runner",
                        lambda _n: (lambda **_kw: {"current_position": "SPRINT.ACTIVE"}))

    ctx = SimpleNamespace(
        chain_dir="/proyectos/client-chat",
        project_root="/proyectos/client-chat",
        project_id="client-chat",
    )
    out = handle_brief(ctx, _payload={"session_id": "ses-1", "file": "a.py"})

    assert out["gates"]["sprint"] == "SPR-REAL"
    assert out["gates"]["resolved_by"] == "session"
    assert out["gates"]["task"] == "T-1"
