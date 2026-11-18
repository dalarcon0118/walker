"""BDD: `load_skill` no puede perder el evento `skill_loaded` en silencio.

Medido 2026-11-18: la tool respondió con el contenido completo de
`code-review-checklist` y el gate `required_skill_gate` siguió bloqueando
porque en la traza no había ningún `skill_loaded` de hoy. El handler ignoraba
el `{"ok": False}` de `push_event` (y el caso `service is None`), así que la
carga parecía exitosa y el gate quedaba insatisfacible sin diagnóstico.

Contrato: si la traza no queda registrada, el resultado lo dice en `warning`;
si queda, no hay warning. Nunca silencio.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from fast_mcp.memories.handlers import knowledge


class FakeSkill:
    name = "code-review-checklist"
    description = "checklist"
    scope = "walker"
    path = "/tmp/skills/code-review-checklist/SKILL.md"

    def body(self) -> str:
        return "# Code Review Checklist"


@pytest.fixture()
def ctx():
    return SimpleNamespace(chain_dir="/tmp/proyecto", project_root="/tmp/proyecto")


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setattr(
        knowledge, "_env_skills", lambda project_root: {FakeSkill.name: FakeSkill()}
    )


def _patch_factory(monkeypatch, services, invalidations):
    """`services` es la secuencia de servicios que devolverá build_trace_service."""
    calls = {"built": 0}

    def build(chain_dir):
        idx = calls["built"]
        calls["built"] += 1
        return services[idx] if idx < len(services) else services[-1]

    monkeypatch.setattr(
        "fast_mcp.features.event_tracing.application.services.trace_factory.build_trace_service",
        build,
    )
    monkeypatch.setattr(
        "fast_mcp.features.event_tracing.application.services.trace_factory.invalidate_trace_service",
        lambda chain_dir: invalidations.append(chain_dir),
    )
    return calls


class FakeTrace:
    def __init__(self, results, default=None):
        self.results = list(results)
        self.default = default if default is not None else {"ok": True}
        self.pushed: list[dict] = []

    def push_event(self, chain_dir, event):
        self.pushed.append(event)
        return self.results.pop(0) if self.results else self.default


def test_si_la_traza_falla_devuelve_warning_y_contenido(ctx, monkeypatch):
    invalidations: list = []
    # Falla siempre (también en el reintento): el warning es obligatorio.
    failing = FakeTrace([], default={"ok": False, "error": "generation mismatch"})
    _patch_factory(monkeypatch, [failing], invalidations)

    out = knowledge.handle_load_skill(ctx, name="code-review-checklist")

    assert out["content"].startswith("# Code Review Checklist")
    assert "warning" in out
    assert "generation mismatch" in out["warning"]
    # Reintentó con un servicio fresco antes de rendirse.
    assert invalidations == ["/tmp/proyecto"]


def test_reintento_exitoso_no_deja_warning(ctx, monkeypatch):
    invalidations: list = []
    first = FakeTrace([{"ok": False, "error": "stale"}])
    second = FakeTrace([{"ok": True, "data": {"event_id": "ev-1"}}])
    calls = _patch_factory(monkeypatch, [first, second], invalidations)

    out = knowledge.handle_load_skill(
        ctx, name="code-review-checklist", task_id="SPEC-002"
    )

    assert out["content"]
    assert "warning" not in out
    assert calls["built"] == 2
    assert invalidations == ["/tmp/proyecto"]
    assert second.pushed[0]["data"]["skill"] == "code-review-checklist"
    assert second.pushed[0]["task_id"] == "SPEC-002"


def test_sin_tracegraph_avisa(ctx, monkeypatch):
    invalidations: list = []
    _patch_factory(monkeypatch, [None], invalidations)

    out = knowledge.handle_load_skill(ctx, name="code-review-checklist")

    assert out["content"]
    assert "TraceGraph no disponible" in out["warning"]


def test_si_la_traza_funciona_no_pone_warning(ctx, monkeypatch):
    invalidations: list = []
    trace = FakeTrace([{"ok": True}])
    _patch_factory(monkeypatch, [trace], invalidations)

    out = knowledge.handle_load_skill(ctx, name="code-review-checklist")

    assert "warning" not in out
    assert trace.pushed[0]["event_type"] == "skill_loaded"
    assert invalidations == []


def test_push_event_que_levanta_no_propaga(ctx, monkeypatch):
    invalidations: list = []

    class Exploding:
        def push_event(self, chain_dir, event):
            raise RuntimeError("db locked")

    _patch_factory(monkeypatch, [Exploding()], invalidations)

    out = knowledge.handle_load_skill(ctx, name="code-review-checklist")

    assert out["content"]
    assert "db locked" in out["warning"]
