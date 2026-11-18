"""BDD: ADR-WALKER-079 — declarar tarea en un sprint pausado.

Decisión: rechazo explícito por defecto (con el comando para reanudar) y
`resume: true` como opt-in. Reanudar implícitamente haría que `paused` signifique
"nadie declaró nada desde la última pausa".
"""

from __future__ import annotations

from fast_mcp.features.tasks.handlers.implement import _ensure_active


class FakeService:
    def __init__(self, status: str) -> None:
        self.spring = {"spring_id": "SPR-1", "status": status}
        self.transitions: list[str] = []

    def transition_spring(self, spring_id: str, status: str) -> dict:
        self.transitions.append(status)
        self.spring["status"] = status
        return {"ok": True, "from": "paused", "to": "active"}

    def get_spring(self, spring_id: str) -> dict:
        return dict(self.spring)


def test_sprint_activo_pasa_sin_tocar_nada():
    svc = FakeService("active")

    ok, msg, spring = _ensure_active(svc, svc.spring, resume=False)

    assert ok and msg == "" and spring["status"] == "active"
    assert svc.transitions == []


def test_sprint_pausado_rechaza_con_comando_accionable():
    svc = FakeService("paused")

    ok, msg, _ = _ensure_active(svc, svc.spring, resume=False)

    assert ok is False
    assert "PAUSADO" in msg and "sprint_status" in msg and "resume:true" in msg
    assert svc.transitions == []  # no reanuda por su cuenta


def test_resume_opt_in_reanuda():
    svc = FakeService("paused")

    ok, msg, spring = _ensure_active(svc, svc.spring, resume=True)

    assert ok and msg == ""
    assert svc.transitions == ["active"]
    assert spring["status"] == "active"


def test_sin_sprint_no_bloquea():
    ok, msg, spring = _ensure_active(None, None, resume=False)

    assert ok and spring is None
