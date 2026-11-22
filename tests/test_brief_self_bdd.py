"""test_brief_self_bdd.py — espejo de conducta en `brief` (ADR-WALKER-084 F0).

Contrato:

- `self` es una PROYECCIÓN del trace de la sesión: writes, verified,
  verify_level, denials (total + por regla), scope_expanded, close_blocked y
  gaps. El agente no declara ninguno de esos campos.
- `verified` desconocido es `None`, nunca `False`: `False` significa "se
  observó un fallo" y habilitaría un cierre sin evidencia (FS-2).
- Una sesión sin eventos no emite la sección (ausencia explícita, no un "todo
  bien"). Un trace roto no tumba el resto del brief.
"""

from __future__ import annotations

from types import SimpleNamespace

from returns.result import Success

import fast_mcp.composition as composition
import fast_mcp.memories.handlers.knowledge as knowledge
from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import (
    CkgReadService,
)
from fast_mcp.features.event_tracing.application.services import trace_factory
from fast_mcp.memories.handlers.knowledge import handle_brief


class _Ctx:
    chain_dir = "/tmp/proyecto"
    project_root = "/tmp/proyecto"
    project_id = "walker"


def _event(event_type: str, data: dict | None = None, session_id: str = "ses_x"):
    return SimpleNamespace(
        event_type=event_type,
        data=data or {},
        metadata={"session_id": session_id},
        files=[],
        task_id="",
        timestamp="2026-11-21T00:00:00+00:00",
    )


def _plan(**over):
    plan = {
        "task": "SPEC-026",
        "acceptance": ["a", "b"],
        "scope": ["a.py"],
        "next": "verificar",
        "approved_by_user": True,
    }
    plan.update(over)
    return plan


def _spec(status: str = "in_progress", **over):
    spec = {
        "spec_id": "SPEC-026",
        "task_context": {"task_id": "SPEC-026"},
        "acceptance_criteria": ["a", "b"],
        "files": ["a.py"],
        "status": status,
        "predicates": {},
    }
    spec.update(over)
    return spec


def _service(plan: dict | None = None, spec: dict | None = None):
    plan = plan or _plan()
    spec = spec or _spec()
    return SimpleNamespace(
        get_session=lambda _sid: {"spring_id": "SPR-INTEG", "flight_plan": plan},
        get_session_plan=lambda _sid: plan,
        get_specs=lambda _spr: [spec],
    )


def _wire(monkeypatch, events: list, plan: dict | None = None, spec: dict | None = None):
    knowledge._SELF_CACHE.clear()
    knowledge._IMPACT_CACHE.clear()
    # chain_dir único por test: la caché de impacto se keyea por (dir, mtime) y
    # dos tests con el mismo path inexistente comparten entrada (mtime 0).
    _Ctx.chain_dir = f"/tmp/proyecto-self-{id(events)}"
    _Ctx.project_root = _Ctx.chain_dir
    monkeypatch.setattr(
        knowledge,
        "_resolve_anchor",
        lambda *_: ({"type": "file", "value": "a.py", "resolved": True}, ["a.py"], None),
    )
    monkeypatch.setattr(
        knowledge,
        "handle_preflight",
        lambda _ctx, **_: {"files": [{"file": "a.py", "callers": 5, "heaviest": []}],
                           "tests": [], "unmapped": []},
    )
    monkeypatch.setattr(CkgReadService, "gather_context",
                        staticmethod(lambda *_a, **_k: Success([])))
    monkeypatch.setattr(composition, "build_sprint_service",
                        lambda _d: _service(plan, spec))
    monkeypatch.setattr(composition, "workflow_tool_runner",
                        lambda _n: (lambda **_kw: {"current_position": "IMPLEMENT.WORK"}))

    class _Trace:
        def load_traces(self, _chain_dir):
            return list(events)

    monkeypatch.setattr(trace_factory, "build_trace_service", lambda _d: _Trace())


def _brief() -> dict:
    return handle_brief(_Ctx(), _payload={"file": "a.py", "session_id": "ses_x"})


def test_self_proyecta_la_conducta_observada(monkeypatch):
    _wire(monkeypatch, [
        _event("session_state", {"writes": 3, "verified": False, "verify_level": "tools"}),
        _event("flight_control_denied", {"gate": "scope"}),
        _event("flight_control_denied", {"gate": "scope"}),
        _event("flight_control_scope_expanded", {"file": "b.py"}),
        _event("close_blocked", {"round": 1}),
    ])

    out = _brief()

    self_state = out["self"]
    assert self_state["writes"] == 3
    assert self_state["verified"] is False
    assert self_state["denials"] == {"total": 2, "by_rule": {"scope": 2}}
    assert self_state["scope_expanded"] == 1
    assert self_state["close_blocked"] == 1
    assert any("sin verificación verde" in g.get("detail", "") for g in self_state["gaps"])
    assert any("cierre" in g.get("detail", "") for g in self_state["gaps"])
    assert any("ampliado" in g.get("detail", "") for g in self_state["gaps"])


def test_verified_desconocido_es_none_no_false(monkeypatch):
    """`False` es un hecho observado; la ausencia de dato no puede mentir."""
    _wire(monkeypatch, [_event("session_state", {"writes": 1})])

    out = _brief()

    assert out["self"]["verified"] is None
    assert any("sin evidencia" in g.get("detail", "") for g in out["self"]["gaps"])


def test_sesion_sin_eventos_no_emite_espejo(monkeypatch):
    _wire(monkeypatch, [])

    out = _brief()

    assert out["self"] == {}, out
    assert not any(m.startswith("self") for m in out["missing"])


def test_trace_roto_no_tumba_el_brief(monkeypatch):
    _wire(monkeypatch, [])

    def boom(_d):
        raise RuntimeError("trace caído")

    monkeypatch.setattr(trace_factory, "build_trace_service", boom)

    out = _brief()

    assert out["self"] == {}
    assert out["gates"]["task"] == "SPEC-026"


def test_un_criterio_sin_predicado_es_un_gap(monkeypatch):
    _wire(
        monkeypatch,
        [_event("session_state", {"writes": 1, "verified": True})],
        plan=_plan(acceptance=["a", "b"]),
        spec=_spec(predicates={"items": [], "unmapped": [{"criterion": "b", "reason": "sin predicado"}]}),
    )

    out = _brief()

    # `plan_diff` publica los criterios sin predicado; el espejo los nombra.
    assert any("criterio" in g.get("detail", "") and "predicado" in g.get("detail", "") for g in out["self"]["gaps"])


def test_el_espejo_no_filtra_eventos_de_otra_sesion(monkeypatch):
    _wire(monkeypatch, [
        _event("session_state", {"writes": 9, "verified": True}, session_id="ses_ajena"),
        _event("session_state", {"writes": 1, "verified": False}, session_id="ses_x"),
    ])

    out = _brief()

    assert out["self"]["writes"] == 1
    assert out["self"]["verified"] is False
