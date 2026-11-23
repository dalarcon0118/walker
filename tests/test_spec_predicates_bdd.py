"""tests/test_spec_predicates_bdd.py — predicados explícitos por criterio.

Hueco medido 2026-11-22: los criterios en prosa quedaban en `unmapped` para
siempre porque no había forma de declarar cómo se verifican. `verify_predicates`
ya leía el bundle de la spec; nadie podía escribirlo.

Contrato:
  - `criteria=[{criterion, predicate}]` agrega el predicado y saca el criterio
    de `unmapped`.
  - `mode=set` reemplaza el bundle.
  - Idempotente por (type, command|target|path).
  - Un predicado inválido no se guarda: el criterio queda en `unmapped`.
  - Sin entradas válidas no se toca la spec (fail-loud).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fast_mcp.features.tasks.handlers import _di
from fast_mcp.features.tasks.handlers import spec_predicates as handler


class _SprintService:
    def __init__(self, spec: dict):
        self.spec = spec
        self.updates: list[dict] = []

    def get_session(self, session_id):
        return {"session_id": session_id, "spring_id": "SPR-1"}

    def get_active_spring(self):
        return {"spring_id": "SPR-1", "status": "active"}

    def list_springs(self):
        return [{"spring_id": "SPR-1", "status": "active"}]

    def get_specs(self, spring_id):
        return [self.spec] if spring_id == "SPR-1" else []

    def get_spec(self, spring_id, spec_id):
        if spring_id == "SPR-1" and spec_id == self.spec["spec_id"]:
            return self.spec
        return None

    def update_spec(self, spring_id, spec_id, updates):
        if spring_id != "SPR-1" or spec_id != self.spec["spec_id"]:
            return None
        self.updates.append(dict(updates))
        self.spec.update(updates)
        return self.spec


def _spec(unmapped: list[dict] | None = None, items: list[dict] | None = None) -> dict:
    return {
        "spec_id": "SPEC-001",
        "status": "in_progress",
        "task_context": {"task_id": "T-1", "session_id": "ses-1"},
        "acceptance_criteria": ["el mapa cita App.Composition", "el build compila"],
        "files": ["project.md"],
        "predicates": {"items": items or [], "unmapped": unmapped or [
            {"criterion": "el mapa cita App.Composition", "reason": "no ejecutable"},
            {"criterion": "el build compila", "reason": "no ejecutable"},
        ]},
    }


@pytest.fixture()
def servicio(monkeypatch):
    svc = _SprintService(_spec())
    monkeypatch.setattr(_di, "_SPRINT_FACTORY", lambda chain_dir: svc)
    return svc


def _ctx():
    return SimpleNamespace(chain_dir="/tmp/proyecto", task_id="T-1",
                           session_id="ses-1", cpg_adapter=None)


def test_adjunta_predicado_y_limpia_unmapped(servicio):
    out = handler.spec_predicates(_ctx(), {
        "session_id": "ses-1",
        "criteria": [{
            "criterion": "el mapa cita App.Composition",
            "predicate": {"type": "command_exit", "command": "grep -q App.Composition project.md"},
        }],
    })

    assert out.ok is True
    data = out.data
    assert data["spec_id"] == "SPEC-001"
    assert data["mapped"] == ["el mapa cita App.Composition"]
    assert [p["command"] for p in data["predicates"]] == ["grep -q App.Composition project.md"]
    assert [u["criterion"] for u in data["unmapped"]] == ["el build compila"]


def test_predicado_string_se_interpreta_como_comando(servicio):
    out = handler.spec_predicates(_ctx(), {
        "criteria": [{"criterion": "el build compila", "predicate": "npx spago build"}],
    })

    assert out.ok is True
    pred = out.data["predicates"][0]
    assert pred["type"] == "command_exit"
    assert pred["command"] == "npx spago build"


def test_es_idempotente(servicio):
    entry = {"criterion": "el build compila", "predicate": {"type": "command_exit", "command": "npx spago build"}}
    handler.spec_predicates(_ctx(), {"criteria": [entry]})
    out = handler.spec_predicates(_ctx(), {"criteria": [entry]})

    assert len(out.data["predicates"]) == 1


def test_mode_set_reemplaza_el_bundle(servicio):
    handler.spec_predicates(_ctx(), {
        "criteria": [{"criterion": "el build compila", "predicate": "npx spago build"}],
    })
    out = handler.spec_predicates(_ctx(), {
        "mode": "set",
        "criteria": [{"criterion": "el mapa cita App.Composition", "predicate": "grep -q App.Composition project.md"}],
    })

    assert [p["command"] for p in out.data["predicates"]] == ["grep -q App.Composition project.md"]


def test_predicado_invalido_no_se_guarda_y_queda_unmapped(servicio):
    out = handler.spec_predicates(_ctx(), {
        "criteria": [{"criterion": "el build compila", "predicate": {"type": "magia"}}],
    })

    assert out.ok is False
    assert "predicado inválido" in out.error.lower() or "invalid" in out.error.lower()
    assert servicio.spec["predicates"]["items"] == []
    assert any(
        u["criterion"] == "el build compila"
        for u in servicio.spec["predicates"]["unmapped"]
    )


def test_sin_criteria_falla_con_la_forma():
    out = handler.spec_predicates(_ctx(), {})

    assert out.ok is False
    assert "criteria" in out.error


def test_criterio_fuera_de_la_spec_se_reporta(servicio):
    out = handler.spec_predicates(_ctx(), {
        "criteria": [{"criterion": "criterio no declarado", "predicate": "true"}],
    })

    assert out.ok is True
    assert out.data["criteria_not_in_spec"] == ["criterio no declarado"]


def test_norm_pliega_acentos_para_comparar_criterios():
    assert handler._norm("los tests del área tocada pasan") == handler._norm(
        "los tests del area tocada pasan"
    )
