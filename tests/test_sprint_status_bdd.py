"""BDD: ADR-WALKER-078 — `paused` reversible.

El orden de SPRING_STATUS_FLOW dejó de ser la regla: la transición se decide con
el grafo SPRING_TRANSITIONS. `active <-> paused` es válido; `reviewing` y
`closed` mantienen su semántica de una sola dirección.
"""

from __future__ import annotations

import pytest

from fast_mcp.features.delta_tracking.domain.entities import Spring
from fast_mcp.features.delta_tracking.domain.spec_service import SprintStatusUpdater
from fast_mcp.features.delta_tracking.application.services.sprint_service import SprintService


class FakeRepo:
    def __init__(self, springs: list[Spring]) -> None:
        self.springs = {s.spring_id: s for s in springs}

    def get_spring(self, spring_id: str) -> Spring | None:
        return self.springs.get(spring_id)

    def save_spring(self, spring: Spring) -> None:
        self.springs[spring.spring_id] = spring


@pytest.mark.parametrize(
    "current,new,expected",
    [
        ("active", "paused", True),
        ("paused", "active", True),
        ("paused", "reviewing", True),
        ("paused", "closed", True),
        ("active", "active", True),
        ("planning", "active", True),
        ("reviewing", "active", False),
        ("closed", "active", False),
        ("closed", "paused", False),
        ("active", "planning", False),
        ("active", "inventado", False),
    ],
)
def test_matriz_de_transiciones(current, new, expected):
    ok, _ = SprintStatusUpdater.can_transition(current, new)
    assert ok is expected


def test_pausar_y_reanudar_en_el_servicio():
    svc = SprintService(repo=FakeRepo([Spring(spring_id="SPR-1", status="active")]))

    pausado = svc.transition_spring("SPR-1", "paused")
    reanudado = svc.transition_spring("SPR-1", "active")

    assert pausado == {"ok": True, "spring_id": "SPR-1", "from": "active", "to": "paused"}
    assert reanudado["from"] == "paused" and reanudado["to"] == "active"


def test_rechazo_explicito_desde_closed():
    svc = SprintService(repo=FakeRepo([Spring(spring_id="SPR-1", status="closed")]))

    result = svc.transition_spring("SPR-1", "active")

    assert result["ok"] is False
    assert "closed" in result["error"]
    assert "terminal" in result["error"]


def test_sprint_desconocido_no_revienta():
    svc = SprintService(repo=FakeRepo([]))

    result = svc.transition_spring("NO-EXISTE", "paused")

    assert result["ok"] is False
    assert "desconocido" in result["error"]
