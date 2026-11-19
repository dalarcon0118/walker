"""BDD: el cierre de sprint liquida o rechaza las tareas abiertas.

Estado real que motivó el fix (2026-11-19): `close_sprint` dejaba una spec
`in_progress` dentro de un sprint `closed`. El contenedor decía "terminado" y
la tarea seguía abierta, sin deuda visible ni forma pública de completarla.

Contrato:
  1. `in_review` + los cuatro artefactos se completa sola al cerrar.
  2. Cualquier otra spec abierta BLOQUEA el cierre y lista qué falta.
  3. `force=True` exige `reason` y deja `closed_with_open_tasks` en el sprint.
  4. `spec_status` es la salida accionable para completar con artefactos.
"""
from __future__ import annotations

import pytest

from fast_mcp.features.delta_tracking.application.services.sprint_service import (
    SprintService,
)
from fast_mcp.features.delta_tracking.domain.entities import Session, Spec, Spring
from fast_mcp.features.tasks.domain.task_registry import ExecutionContext
from fast_mcp.features.tasks.handlers import _di
from fast_mcp.features.tasks.handlers.sprint_planning import _build_handlers
from fast_mcp.features.tasks.handlers.sprint_status import sprint_status
from fast_mcp.features.tasks.handlers.spec_status import spec_status
from fast_mcp.features.tasks.side_effects import sprint as sprint_side_effects

ARTIFACTS = {
    "completed_at": "2026-11-19T13:00:00+00:00",
    "commit_hash": "2a78e1e",
    "review_verdict": "approved",
    "evidence": ["pytest: 1339 passed"],
}


class InMemoryRepo:
    def __init__(self, spring: Spring, specs: list[Spec] | None = None) -> None:
        self.springs = {spring.spring_id: spring}
        self.specs = {(s.spring_id, s.spec_id): s for s in (specs or [])}

    def get_spring(self, spring_id):
        return self.springs.get(spring_id)

    def save_spring(self, spring):
        self.springs[spring.spring_id] = spring

    def list_springs(self, status=""):
        out = list(self.springs.values())
        return [s for s in out if s.status == status] if status else out

    def get_specs(self, spring_id, status=""):
        out = [s for (sid, _), s in self.specs.items() if sid == spring_id]
        return [s for s in out if s.status == status] if status else out

    def get_spec(self, spring_id, spec_id):
        return self.specs.get((spring_id, spec_id))

    def save_spec(self, spec):
        self.specs[(spec.spring_id, spec.spec_id)] = spec

    def delete_spec(self, spring_id, spec_id):
        self.specs.pop((spring_id, spec_id), None)

    def get_session(self, session_id):
        if session_id == "s1":
            return Session(session_id="s1", spring_id="SPR-1", status="active")
        return None

    def get_active_spring(self):
        return next(
            (s for s in self.springs.values() if s.status == "active"), None
        )


def _spec(spec_id="SPEC-001", status="in_progress", **over) -> Spec:
    data = {
        "spring_id": "SPR-1",
        "spec_id": spec_id,
        "spec_index": max(
            0, int(spec_id.split("-")[-1]) - 1
        ) if spec_id.split("-")[-1].isdigit() else 0,
        "objective": "objetivo",
        "status": status,
        "files": ["a.py"],
        "task_context": {"task_id": f"task-{spec_id}", "session_id": "s1"},
        "evidence": [],
        "completed_at": "",
        "commit_hash": "",
        "review_verdict": "",
    }
    data.update(over)
    return Spec.from_dict(data)


def _service(specs, spring_status="active") -> SprintService:
    spring = Spring(spring_id="SPR-1", goal="g", status=spring_status, session_id="s1")
    return SprintService(project_root="/tmp", repo=InMemoryRepo(spring, specs))


def _ctx() -> ExecutionContext:
    return ExecutionContext.create(chain_dir="/tmp/proj", session_id="s1")


@pytest.fixture
def sprint_service_ctx():
    """Inyecta el servicio en los handlers y lo restaura al terminar."""
    yield
    _di.configure_sprint(None)


# ── 1. in_review + artefactos se liquida sola ───────────────────────────


def test_in_review_con_artefactos_se_completa_al_cerrar():
    svc = _service([_spec(status="in_review", **ARTIFACTS)])

    result = svc.close_spring("SPR-1")

    assert result["ok"] is True
    assert result["settled"] == ["SPEC-001"]
    assert result["open"] == []
    assert svc.get_spring("SPR-1")["status"] == "closed"
    assert svc.get_spec("SPR-1", "SPEC-001")["status"] == "completed"


def test_cierre_es_idempotente():
    svc = _service([_spec(status="in_review", **ARTIFACTS)])
    svc.close_spring("SPR-1")

    again = svc.close_spring("SPR-1")

    assert again["ok"] is True and again["idempotent"] is True


# ── 2. tarea abierta bloquea y dice qué falta ───────────────────────────


def test_tarea_in_progress_bloquea_el_cierre():
    svc = _service([_spec(status="in_progress")])

    result = svc.close_spring("SPR-1")

    assert result["ok"] is False
    assert "sin completar" in result["error"]
    assert result["open"][0]["spec_id"] == "SPEC-001"
    assert any("commit_hash" in item for item in result["open"][0]["missing"])
    assert svc.get_spring("SPR-1")["status"] == "active"


def test_in_review_sin_artefactos_no_se_liquida_y_bloquea():
    svc = _service([_spec(status="in_review")])

    result = svc.close_spring("SPR-1")

    assert result["ok"] is False
    assert result["settled"] == []
    assert svc.get_spec("SPR-1", "SPEC-001")["status"] == "in_review"


# ── 3. force exige motivo y deja la deuda ───────────────────────────────


def test_force_sin_reason_falla():
    svc = _service([_spec(status="in_progress")])

    result = svc.close_spring("SPR-1", force=True)

    assert result["ok"] is False
    assert "reason" in result["error"]
    assert svc.get_spring("SPR-1")["status"] == "active"


def test_force_con_reason_cierra_y_audita_la_deuda():
    svc = _service([_spec(status="in_progress")])

    result = svc.close_spring("SPR-1", force=True, reason="sprint abandonado")

    assert result["ok"] is True and result["forced"] is True
    spring = svc.get_spring("SPR-1")
    assert spring["status"] == "closed"
    context = spring.get("context") or {}
    assert context["close_reason"] == "sprint abandonado"
    assert context["closed_with_open_tasks"][0]["spec_id"] == "SPEC-001"


# ── 4. spec_status es la salida accionable ──────────────────────────────


def test_spec_status_completed_sin_artefactos_falla(sprint_service_ctx):
    svc = _service([_spec(status="in_progress")])
    _di.configure_sprint(lambda chain_dir: svc)

    result = spec_status(_ctx(), {"spec_id": "SPEC-001", "status": "completed"})

    assert result.ok is False
    assert "artefactos" in result.error.lower() or "artifacts" in result.error.lower()


def test_spec_status_completed_con_artefactos_y_cierre(sprint_service_ctx):
    svc = _service([_spec(status="in_progress")])
    _di.configure_sprint(lambda chain_dir: svc)

    result = spec_status(
        _ctx(), {"spec_id": "SPEC-001", "status": "completed", **ARTIFACTS}
    )

    assert result.ok is True
    assert result.data["status"] == "completed"
    assert svc.get_spec("SPR-1", "SPEC-001")["commit_hash"] == "2a78e1e"

    closed = svc.close_spring("SPR-1")
    assert closed["ok"] is True and closed["open"] == []


# ── 5. el handler de close y el efecto usan la misma invariante ─────────


def test_close_sprint_handler_falla_con_tarea_abierta():
    svc = _service([_spec(status="in_progress")])
    _, _, _, close = _build_handlers(svc)

    result = close(_ctx(), {"session_id": "s1"})

    assert result.ok is False
    assert "sin completar" in result.error
    assert svc.get_spring("SPR-1")["status"] == "active"


def test_sprint_status_closed_respeta_el_invariante(sprint_service_ctx):
    svc = _service([_spec(status="in_progress")])
    _di.configure_sprint(lambda chain_dir: svc)

    result = sprint_status(_ctx(), {"session_id": "s1", "status": "closed"})

    assert result.ok is False
    assert svc.get_spring("SPR-1")["status"] == "active"


def test_archive_sprint_effect_falla_con_tarea_abierta():
    svc = _service([_spec(status="in_progress")])

    result = sprint_side_effects.archive_sprint(
        "/tmp/x", {}, sprint_service=svc, spring_id="SPR-1"
    )

    assert result["ok"] is False
    assert svc.get_spring("SPR-1")["status"] == "active"


def test_archive_sprint_effect_cierra_sin_tareas_abiertas():
    svc = _service([_spec(status="completed", **ARTIFACTS)])

    result = sprint_side_effects.archive_sprint(
        "/tmp/x", {}, sprint_service=svc, spring_id="SPR-1"
    )

    assert result["ok"] is True and result["archived"] is True
    assert svc.get_spring("SPR-1")["status"] == "closed"
