"""BDD: se emite un evento de Trace al leer/proyectar el contrato (F0c)."""

from __future__ import annotations

from fast_mcp.memories.handlers import knowledge


def _spec_with_contract():
    return {
        "spec_id": "SPEC-003",
        "task_context": {
            "work_contract": {
                "version": "1",
                "goal": "g",
                "scope": ["a.py"],
                "criteria": ["c"],
                "verification": [{"criterion": "c", "check": "test"}],
                "budget": {"max_files": 1},
                "recurrence": {"level": "full"},
            }
        },
    }


def test_trace_contract_emite_evento(monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(
        knowledge, "_CONTRACT_TRACE_SINK", lambda chain, ev: events.append(ev)
    )

    knowledge._trace_contract("/tmp/x", "SPEC-003", "SPEC-003", True)

    assert len(events) == 1
    assert events[0]["event_type"] == "work_contract_read"
    assert events[0]["data"]["present"] is True
    assert events[0]["data"]["spec_id"] == "SPEC-003"


def test_preflight_emite_trace_al_proyectar(monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(
        knowledge, "_CONTRACT_TRACE_SINK", lambda chain, ev: events.append(ev)
    )
    monkeypatch.setattr(knowledge, "_find_task_spec", lambda chain, tid: _spec_with_contract())

    class _Ctx:
        chain_dir = "/tmp/x"
        project_id = "walker"
        task_id = "SPEC-003"
        project_root = "/tmp/x"

        def code_context_service(self):
            raise RuntimeError("sin cpg")

        def code_analysis_service(self):
            raise RuntimeError("sin ca")

    knowledge.handle_preflight(_Ctx(), _payload={"files": ["a.py"], "task_id": "SPEC-003"})

    assert any(e["event_type"] == "work_contract_read" for e in events)
