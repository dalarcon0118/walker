"""BDD: `transform_spec` completa los artefactos de cierre (F1b fix).

Al marcar `completed`, `completed_at` y `commit_hash` no deben depender de que
el agente los haya enviado en REPORT_GATE. Si faltan, se generan acá.
"""

from __future__ import annotations

from fast_mcp.features.tasks.side_effects.spec import transform_spec


class _Svc:
    def __init__(self):
        self.calls: dict = {}

    def update_spec(self, spring_id, spec_id, updates):
        self.calls = {"spring_id": spring_id, "spec_id": spec_id, **updates}
        return {"ok": True}


def test_completa_artefactos_faltantes():
    svc = _Svc()
    out = transform_spec(
        "/tmp",
        {
            "status": "completed",
            "fields": {"completed_at": "", "commit_hash": ""},
            "spring_id": "SPR-1",
            "spec_id": "SPEC-X",
        },
        sprint_service=svc,
    )

    assert out["ok"] is True
    assert svc.calls["status"] == "completed"
    assert svc.calls["completed_at"]  # generado
    assert svc.calls["commit_hash"]  # "local" o git head


def test_respeta_artefactos_provistos():
    svc = _Svc()
    out = transform_spec(
        "/tmp",
        {
            "status": "completed",
            "fields": {"completed_at": "2020-01-01T00:00:00Z", "commit_hash": "abc123"},
            "spring_id": "SPR-1",
            "spec_id": "SPEC-X",
        },
        sprint_service=svc,
    )

    assert out["ok"] is True
    assert svc.calls["completed_at"] == "2020-01-01T00:00:00Z"
    assert svc.calls["commit_hash"] == "abc123"


def test_in_review_no_fuerza_artefactos():
    svc = _Svc()
    out = transform_spec(
        "/tmp",
        {"status": "in_review", "fields": {}, "spring_id": "SPR-1", "spec_id": "SPEC-X"},
        sprint_service=svc,
    )

    assert out["ok"] is True
    assert "completed_at" not in svc.calls
    assert "commit_hash" not in svc.calls
