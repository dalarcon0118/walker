"""BDD: los efectos laterales que el grafo declara existen y hacen lo que dicen.

Motivación medida (2026-11-18): 12 de 15 efectos declarados no tenían handler.
El motor devolvía "unknown side effect" y, como casi todos eran opcionales, el
workflow seguía como si nada: el CKG, el trace y el índice nunca se movían.
"""
from __future__ import annotations

import glob
import json
import os
from types import SimpleNamespace

import pytest

from fast_mcp.features.tasks.domain.side_effect_registry import SideEffectRegistry
from fast_mcp.features.tasks.side_effects import (
    events,
    indexing,
    knowledge,
    register_all_side_effects,
    sprint,
)


class FakeSprintService:
    def __init__(self, specs=None, spring=None, session=None):
        self.springs = {}
        if spring:
            self.springs[spring["spring_id"]] = dict(spring)
        self.specs = {s["spec_id"]: dict(s) for s in (specs or [])}
        self.session = session or {}
        self.updates: list[tuple] = []
        self.transitions: list[tuple] = []

    def get_spring(self, spring_id):
        return self.springs.get(spring_id)

    def get_session(self, session_id):
        return self.session

    def get_specs(self, spring_id, status=""):
        return list(self.specs.values())

    def get_spec(self, spring_id, spec_id):
        return self.specs.get(spec_id)

    def update_spring(self, spring_id, data):
        self.updates.append((spring_id, dict(data)))
        self.springs.setdefault(spring_id, {"spring_id": spring_id}).update(dict(data))

    def update_spec(self, spring_id, spec_id, updates):
        self.updates.append((spring_id, spec_id, dict(updates)))
        if spec_id in self.specs:
            self.specs[spec_id].update(dict(updates))
        return self.specs.get(spec_id)

    def transition_spring(self, spring_id, status):
        self.transitions.append((spring_id, status))
        current = str((self.springs.get(spring_id) or {}).get("status") or "planning")
        if status == "closed" and current == "closed":
            return {"ok": True, "from": current, "to": status}
        self.springs.setdefault(spring_id, {"spring_id": spring_id})["status"] = status
        return {"ok": True, "from": current, "to": status}

    def list_springs(self):
        return list(self.springs.values())


def _spring(**over):
    base = {"spring_id": "SPR-T", "goal": "g", "status": "planning", "context": {}}
    base.update(over)
    return base


def _specs():
    return [
        {"spec_id": "SPEC-001", "objective": "uno", "status": "completed"},
        {"spec_id": "SPEC-002", "objective": "dos", "status": "specified"},
        {"spec_id": "SPEC-003", "objective": "", "status": "specified"},
    ]


# ── Conformidad grafo ↔ registro ────────────────────────────────────────

def test_todos_los_efectos_del_grafo_estan_registrados():
    registry = SideEffectRegistry()
    register_all_side_effects(registry)
    have = set(registry.list_effects())

    declared: set[str] = set()

    def walk(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in ("side_effects", "effects") and isinstance(value, (list, str)):
                    for item in value if isinstance(value, list) else [value]:
                        name = (
                            item.get("effect") or item.get("name") or item.get("type")
                            if isinstance(item, dict)
                            else str(item)
                        )
                        if name:
                            declared.add(str(name))
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    for path in glob.glob("data/workflow/**/*.json", recursive=True):
        walk(json.load(open(path, encoding="utf-8")))

    assert declared, "no se declaró ningún efecto: el test no está midiendo nada"
    missing = sorted(declared - have)
    assert missing == [], f"efectos declarados sin handler: {missing}"


# ── sprint_status ───────────────────────────────────────────────────────

def test_sprint_status_respeta_la_decision_del_gate():
    svc = FakeSprintService(spring=_spring())
    out = sprint.sprint_status(
        "/tmp/x",
        {"status": "active", "decision": "approve"},
        sprint_service=svc,
        spring_id="SPR-T",
        fields={"decision": "refine"},
    )
    assert out["ok"] is True and out["skipped"] is True
    assert svc.transitions == []

    out = sprint.sprint_status(
        "/tmp/x",
        {"status": "active", "decision": "approve"},
        sprint_service=svc,
        spring_id="SPR-T",
        fields={"decision": "approve"},
    )
    assert out["ok"] is True
    assert svc.transitions == [("SPR-T", "active")]


def test_sprint_status_propaga_rechazo():
    svc = FakeSprintService(spring=_spring(status="closed"))

    def reject(spring_id, status):
        return {"ok": False, "error": "Cannot move sprint status"}

    svc.transition_spring = reject
    out = sprint.sprint_status("/tmp/x", {"status": "active"}, sprint_service=svc, spring_id="SPR-T")

    assert out["ok"] is False
    assert "Cannot move" in out["error"]


# ── advance_sprint_task ──────────────────────────────────────────────────

def test_advance_sprint_task_avanza_y_clampa():
    svc = FakeSprintService(specs=_specs(), spring=_spring())
    out = sprint.advance_sprint_task(
        "/tmp/x", {}, sprint_service=svc, spring_id="SPR-T",
        state=SimpleNamespace(context={"task_id": "SPEC-001"}),
    )
    assert out["ok"] is True
    assert out["next_spec_id"] == "SPEC-002"
    assert out["current_task_index"] == 1
    assert svc.springs["SPR-T"]["context"]["task_id"] == "SPEC-002"

    last = sprint.advance_sprint_task(
        "/tmp/x", {}, sprint_service=svc, spring_id="SPR-T",
        state=SimpleNamespace(context={"task_id": "SPEC-003"}),
    )
    assert last["current_task_index"] == 2
    assert last["next_spec_id"] == "SPEC-003"
    assert last["all_done"] is True
    assert last["previous_spec_id"] == "SPEC-003"


# ── archive_sprint ──────────────────────────────────────────────────────

def test_archive_sprint_cierra_y_es_idempotente():
    svc = FakeSprintService(spring=_spring(status="reviewing"))
    first = sprint.archive_sprint("/tmp/x", {}, sprint_service=svc, spring_id="SPR-T")
    again = sprint.archive_sprint("/tmp/x", {}, sprint_service=svc, spring_id="SPR-T")

    assert first["ok"] is True and first["archived"] is True
    assert again["idempotent"] is True
    assert svc.springs["SPR-T"]["status"] == "closed"


# ── index_sprint_tasks / rebuild_sprint_index ───────────────────────────

def test_index_sprint_tasks_reporta_anomalias():
    svc = FakeSprintService(specs=_specs(), spring=_spring())
    out = sprint.index_sprint_tasks("/tmp/x", {}, sprint_service=svc, spring_id="SPR-T")

    assert out["ok"] is True
    assert out["indexed"] == 3
    assert out["source"] == "delta"
    assert [a["spec_id"] for a in out["anomalies"]] == ["SPEC-003"]


def test_rebuild_sprint_index_recorre_todos_los_sprints():
    svc = FakeSprintService(specs=_specs(), spring=_spring())
    svc.springs["SPR-2"] = _spring(spring_id="SPR-2")
    out = sprint.rebuild_sprint_index("/tmp/x", {}, sprint_service=svc)

    assert out["ok"] is True
    assert out["springs"] == 2
    # SPEC-003 aparece una vez por sprint en la pasada global.
    assert len(out["anomalies"]) == 2


# ── create_adr ──────────────────────────────────────────────────────────

def test_create_adr_ignora_si_no_es_decision():
    svc = FakeSprintService(specs=_specs(), spring=_spring())
    out = sprint.create_adr("/tmp/x", {"approval_type": "standard"}, sprint_service=svc, spring_id="SPR-T")

    assert out["ok"] is True and out["skipped"] is True
    assert "approval_type" in out["reason"]


def test_create_adr_crea_y_enlaza_la_spec(monkeypatch):
    svc = FakeSprintService(specs=_specs(), spring=_spring())
    saved: dict = {}

    def fake_save(chain_dir, nodes, task_id="", session_id=""):
        saved.update({"nodes": nodes, "task_id": task_id})
        return {"ok": True, "node_ids": ["decision-042"]}

    monkeypatch.setattr("fast_mcp.composition.ckg_save_nodes", fake_save)
    monkeypatch.setattr(
        "fast_mcp.features.tasks.side_effects._context.ckg",
        lambda chain_dir: SimpleNamespace(query=lambda f: []),
    )

    out = sprint.create_adr(
        "/tmp/x", {"approval_type": "decision"}, sprint_service=svc, spring_id="SPR-T",
        ctx=SimpleNamespace(task_id="SPEC-002", session_id="s1"),
    )

    assert out["ok"] is True, out
    assert out["adr_id"] == "decision-042"
    assert out["related_decisions"] == ["decision-042"]
    assert svc.specs["SPEC-002"]["related_decisions"] == ["decision-042"]
    assert saved["nodes"][0]["metadata"]["spec_id"] == "SPEC-002"
    assert saved["nodes"][0]["type"] == "decision"


# ── emit_event ──────────────────────────────────────────────────────────

def test_emit_event_sin_trace_no_rompe(monkeypatch):
    monkeypatch.setattr(
        "fast_mcp.composition.trace_tool_runner",
        lambda name: (lambda **kw: {"error": "Could not initialize CognitiveGraph"}),
    )
    out = events.emit_event("/tmp/x", {"event": "task_completed"})

    assert out["ok"] is True and out["skipped"] is True


def test_emit_event_empuja_al_trace(monkeypatch):
    pushed: list = []

    def fake_runner(name):
        def run(**kwargs):
            pushed.append(kwargs)
            return {"ok": True, "event_id": "evt-1"}

        return run

    monkeypatch.setattr("fast_mcp.composition.trace_tool_runner", fake_runner)
    out = events.emit_event("/tmp/x", {"event": "sprint_planned"}, ctx=SimpleNamespace(task_id="SPEC-002"))

    assert out["ok"] is True and out["emitted"] is True
    assert pushed[0]["event_type"] == "sprint_planned"
    assert pushed[0]["task_id"] == "SPEC-002"


# ── indexing ────────────────────────────────────────────────────────────

def test_index_code_no_reporta_exito_si_no_actualizo(monkeypatch):
    monkeypatch.setattr(
        "fast_mcp.composition.memory_tool_runner",
        lambda name: (lambda **kw: {"updated": False}),
    )
    out = indexing.index_code("/tmp/x", {})

    assert out["ok"] is False
    assert out["indexed"] is False


def test_build_project_graph_mapea_built(monkeypatch):
    monkeypatch.setattr(
        "fast_mcp.composition.memory_tool_runner",
        lambda name: (lambda **kw: {"built": True}),
    )
    out = indexing.build_project_graph("/tmp/x", {})

    assert out["ok"] is True and out["built"] is True


# ── knowledge ───────────────────────────────────────────────────────────

def test_documenting_knowledge_es_idempotente_si_el_nodo_ya_guardo():
    state = SimpleNamespace(sub_state_data={"CKG_SAVE": {"fields": {"node_id": "design-001"}}})
    out = knowledge.documenting_knowledge("/tmp/x", {}, state=state)

    assert out["ok"] is True and out["skipped"] is True
    assert out["node_id"] == "design-001"


def test_documenting_knowledge_encuentra_el_node_id_no_el_ultimo_sub_estado():
    # `advance` corre el efecto con el estado ya movido: el sub-estado más
    # nuevo tiene `project_id` y el `node_id` quedó en el anterior.
    state = SimpleNamespace(
        sub_state_data={
            "CKG_SAVE": {"fields": {"node_id": "design-001"}},
            "NEXT_GATE": {"fields": {"project_id": "walker", "decision": ""}},
        }
    )

    out = knowledge.documenting_knowledge("/tmp/x", {}, state=state)

    assert out["skipped"] is True
    assert out["node_id"] == "design-001"


def test_documenting_knowledge_persiste_la_decision(monkeypatch):
    saved: dict = {}

    def fake_save(chain_dir, nodes, task_id="", session_id=""):
        saved.update({"nodes": nodes, "task_id": task_id, "session_id": session_id})
        return {"ok": True, "node_ids": ["decision-7"]}

    monkeypatch.setattr("fast_mcp.composition.ckg_save_nodes", fake_save)
    monkeypatch.setattr(
        "fast_mcp.features.tasks.side_effects._context.ckg",
        lambda chain_dir: SimpleNamespace(query=lambda f: []),
    )
    state = SimpleNamespace(
        sub_state_data={"DESIGN_REVIEW": {"fields": {"decision": "aprobar", "evidence": "tests verdes"}}}
    )

    out = knowledge.documenting_knowledge(
        "/tmp/x", {}, state=state, ctx=SimpleNamespace(task_id="SPEC-002", session_id="s1")
    )

    assert out["ok"] is True, out
    assert out["source"] == "state-decision"
    assert saved["nodes"][0]["label"] == "aprobar"
    assert saved["nodes"][0]["metadata"]["spec_id"] == "SPEC-002"
    assert saved["session_id"] == "s1"


def test_documenting_knowledge_no_duplica_decision_existente(monkeypatch):
    monkeypatch.setattr(
        "fast_mcp.features.tasks.side_effects._context.ckg",
        lambda chain_dir: SimpleNamespace(query=lambda f: [{"_id": "decision-1"}]),
    )
    state = SimpleNamespace(sub_state_data={"DESIGN_REVIEW": {"fields": {"decision": "aprobar"}}})

    out = knowledge.documenting_knowledge("/tmp/x", {}, state=state, task_id="SPEC-002")

    assert out["skipped"] is True
    assert out["node_id"] == "decision-1"


@pytest.mark.parametrize(
    "event,expected",
    [
        ("implementation_start", "pending_update"),
        ("knowledge_captured", "up_to_date"),
        ("sprint_retro_completed", "up_to_date"),
    ],
)
def test_update_knowledge_status_mapea_eventos(event, expected):
    svc = FakeSprintService(spring=_spring(ckg_status="up_to_date"))
    out = knowledge.update_knowledge_status(
        "/tmp/x", {"event": event}, sprint_service=svc, spring_id="SPR-T"
    )

    assert out["ok"] is True
    assert out["status"] == expected
    assert svc.springs["SPR-T"]["ckg_status"] == expected


def test_update_knowledge_status_es_idempotente():
    svc = FakeSprintService(spring=_spring(ckg_status="up_to_date"))
    out = knowledge.update_knowledge_status(
        "/tmp/x", {"event": "knowledge_captured"}, sprint_service=svc, spring_id="SPR-T"
    )

    assert out["idempotent"] is True
    assert svc.updates == []


def test_remove_knowledge_borra_por_task_id(monkeypatch):
    called: dict = {}

    def fake_delete(chain_dir, key, value):
        called.update({"key": key, "value": value})
        return {"ok": True, "nodes_removed": 2}

    monkeypatch.setattr("fast_mcp.composition.ckg_delete_by_metadata", fake_delete)
    out = knowledge.remove_knowledge("/tmp/x", {}, ctx=SimpleNamespace(task_id="SPEC-002"))

    assert out["ok"] is True
    assert called == {"key": "task_id", "value": "SPEC-002"}
