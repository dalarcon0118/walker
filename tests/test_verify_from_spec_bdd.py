"""test_verify_from_spec_bdd.py — los predicados de la spec se ejecutan.

El workflow derivaba predicados y los guardaba en `spec.predicates.items`, pero
`verify_predicates` los esperaba del llamador: nadie los pasaba, así que el gate
de VERIFY fallaba por falta de datos teniéndolos a mano.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fast_mcp.features.tasks.handlers import _di, verify as verify_handlers


def _ctx(chain_dir="/tmp/proyecto"):
    return SimpleNamespace(chain_dir=chain_dir, task_id="", session_id="s", cpg_adapter=None)


@pytest.fixture()
def spec(monkeypatch):
    state = {"spec": None}
    monkeypatch.setattr(_di, "current_spec", lambda chain_dir, task_id="", prefer_status=("in_progress",): state["spec"])
    return state


def test_lee_los_predicados_de_la_spec(spec, tmp_path):
    spec["spec"] = {
        "spec_id": "SPEC-007", "status": "in_progress",
        "predicates": {"items": [{"type": "command_exit", "command": "true"}], "unmapped": []},
    }

    out = verify_handlers.verify_predicates(_ctx(str(tmp_path)), {})

    assert out.ok
    assert out.data["predicates_source"] == "spec:SPEC-007"
    assert out.data["all_satisfied"] is True
    assert out.data["total"] == 1


def test_el_caller_manda_si_pasa_predicados(spec, tmp_path):
    spec["spec"] = None

    out = verify_handlers.verify_predicates(
        _ctx(str(tmp_path)), {"predicates": [{"type": "command_exit", "command": "true"}]}
    )

    assert out.ok
    assert out.data["predicates_source"] == "caller"


def test_sin_predicados_ejecutables_falla_con_el_motivo(spec):
    """Los criterios existen pero no se pueden correr: hay que decirlo."""
    spec["spec"] = {
        "spec_id": "SPEC-008", "status": "in_progress",
        "predicates": {"items": [], "unmapped": [
            {"criterion": "no cambies la API", "reason": "no ejecutable sin un test nuevo"}
        ]},
    }

    out = verify_handlers.verify_predicates(_ctx(), {})

    assert not out.ok
    assert "no ejecutable sin un test nuevo" in out.error


def test_los_unmapped_viajan_en_el_resultado(spec, tmp_path):
    """Un gate verde con criterios sin chequear no es un gate completo."""
    spec["spec"] = {
        "spec_id": "SPEC-009", "status": "in_progress",
        "predicates": {
            "items": [{"type": "command_exit", "command": "true"}],
            "unmapped": [{"criterion": "no cambies la API", "reason": "sin test"}],
        },
    }

    out = verify_handlers.verify_predicates(_ctx(str(tmp_path)), {})

    assert out.ok
    assert out.data["unmapped"][0]["criterion"] == "no cambies la API"


def test_sin_spec_sigue_fallando(spec):
    spec["spec"] = None

    out = verify_handlers.verify_predicates(_ctx(), {})

    assert not out.ok
    assert "predicates vacíos" in out.error


def test_current_spec_toma_la_mas_reciente_en_progreso(monkeypatch):
    class _Svc:
        def get_active_spring(self):
            return {"spring_id": "SPR-1"}

        def list_springs(self):
            return [{"spring_id": "SPR-1"}]

        def get_specs(self, sid):
            return [
                {"spec_id": "SPEC-001", "status": "done", "updated_at": "2026-01-03"},
                {"spec_id": "SPEC-002", "status": "in_progress", "updated_at": "2026-01-01"},
                {"spec_id": "SPEC-003", "status": "in_progress", "updated_at": "2026-01-05"},
            ]

    monkeypatch.setattr(_di, "_SPRINT_FACTORY", lambda chain: _Svc())

    assert _di.current_spec("/tmp/x")["spec_id"] == "SPEC-003"


def test_current_spec_por_id_exacto_gana_a_la_mas_reciente(monkeypatch):
    class _Svc:
        def get_active_spring(self):
            return {"spring_id": "SPR-1"}

        def list_springs(self):
            return [{"spring_id": "SPR-1"}]

        def get_specs(self, sid):
            return [
                {"spec_id": "SPEC-001", "status": "in_progress", "updated_at": "2026-01-01",
                 "task_context": {"task_id": "T-101"}},
                {"spec_id": "SPEC-002", "status": "in_progress", "updated_at": "2026-01-09",
                 "task_context": {"task_id": "T-102"}},
            ]

    monkeypatch.setattr(_di, "_SPRINT_FACTORY", lambda chain: _Svc())

    assert _di.current_spec("/tmp/x", task_id="T-101")["spec_id"] == "SPEC-001"
    assert _di.current_spec("/tmp/x")["spec_id"] == "SPEC-002", "sin id cae a la más reciente"


def test_el_orquestador_no_usa_el_nombre_de_la_accion_como_task_id():
    """Una acción no es una tarea: ese fallback rompía specs y anclas."""
    from types import SimpleNamespace

    from fast_mcp.features.workflow.application.orchestrator import OrchestrationService
    from fast_mcp.features.workflow.domain.entities import SubState, WorkflowState

    captured: dict = {}

    class _Registry:
        def has(self, action):
            return True

        def execute(self, action, ctx, data):
            captured.update(data)
            return SimpleNamespace(ok=True, data={}, error="")

    orch = OrchestrationService.__new__(OrchestrationService)
    orch._bundle = SimpleNamespace(chain_dir="/tmp/x", task_registry=_Registry())

    sub = SubState(key="X", label="X", action="capture_baseline")
    state = WorkflowState(current_node="N", current_sub_state="X")

    orch._execute_action(SimpleNamespace(task_id="", session_id=""), sub, state, {})

    assert captured["task_id"] == "", "sin tarea declarada, task_id queda vacío"
    assert captured["action"] == "capture_baseline", "la acción viaja aparte"
