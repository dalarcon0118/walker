"""BDD: ADR-WALKER-077 — sprint activo por sesión.

Varios sprints `active` en un proyecto son legítimos si cada uno pertenece a una
sesión distinta. La anomalía es la falta de atribución (sin session_id o con ids
duplicados), que es lo que permite el sprint hijacking.
"""

from __future__ import annotations

import logging

from fast_mcp.features.delta_tracking.infrastructure.cognitive_delta_repo import (
    CognitiveDeltaRepository,
)


class FakeCG:
    """Solo lo que usa `get_active_spring`: search con filtros exactos."""

    def __init__(self, docs: list[dict]) -> None:
        self.docs = docs

    def search(self, index: str, filters: dict) -> dict:
        hits = [
            {"_source": d}
            for d in self.docs
            if all(d.get(k) == v for k, v in filters.items())
        ]
        return {"hits": {"hits": hits}}


def _spring(spring_id: str, session_id: str = "", updated_at: str = "", informal: bool = False) -> dict:
    return {
        "type": "spring",
        "spring_id": spring_id,
        "status": "active",
        "session_id": session_id,
        "updated_at": updated_at,
        "informal": informal,
    }


def _repo(docs: list[dict]) -> CognitiveDeltaRepository:
    return CognitiveDeltaRepository(FakeCG(docs))


def _warnings(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


def test_activos_con_sesiones_distintas_no_es_anomalia(caplog):
    caplog.set_level(logging.WARNING)
    repo = _repo(
        [
            _spring("SPR-1", "ses-1", "2026-01-01"),
            _spring("SPR-2", "ses-2", "2026-01-02"),
            _spring("SPR-3", "ses-3", "2026-01-03"),
        ]
    )

    active = repo.get_active_spring()

    assert active is not None and active.spring_id == "SPR-3"  # el más reciente
    assert "sin atribución" not in _warnings(caplog)


def test_activo_sin_sesion_es_anomalia(caplog):
    caplog.set_level(logging.WARNING)
    repo = _repo([_spring("SPR-1", "", "2026-01-01"), _spring("SPR-2", "ses-2", "2026-01-02")])

    repo.get_active_spring()

    assert "sin atribución" in _warnings(caplog)


def test_ids_de_sesion_duplicados_es_anomalia(caplog):
    caplog.set_level(logging.WARNING)
    repo = _repo(
        [_spring("SPR-1", "ses-1", "2026-01-01"), _spring("SPR-2", "ses-1", "2026-01-02")]
    )

    repo.get_active_spring()

    assert "sin atribución" in _warnings(caplog)


def test_el_mas_reciente_gana_entre_iguales():
    repo = _repo([_spring("VIEJO", "ses-1", "2026-01-01"), _spring("NUEVO", "ses-1", "2026-02-01")])

    assert repo.get_active_spring().spring_id == "NUEVO"
