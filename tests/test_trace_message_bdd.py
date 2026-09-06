"""tests/test_trace_message_bdd.py — T-14: wiring de la tool `trace_message`.

BDD (cableado fase-3): trace_message está expuesta como tool del MCP,
despachada vía trace_tool_runner, con schema SSOT (role enum) y description.
La semántica de persistencia (user/agent, fail-fast, roundtrip sobre
CognitiveGraph real) se cubre en tests/test_trace_messages_ckg_bdd.py.
"""
from __future__ import annotations


class FakeCG:
    """Minimal CognitiveGraph double: search + store sobre dict en memoria."""

    def __init__(self):
        self._store: dict[str, dict] = {}

    def search(self, index, query):
        hits = [{"_id": eid, "_source": data} for eid, data in self._store.items()]
        return {"hits": {"hits": hits}}

    def store(self, index, eid, data):
        self._store[eid] = data


def _handler_ctx():
    from fast_mcp.features.event_tracing.application.services.trace_service import (
        TraceService,
    )
    from fast_mcp.features.event_tracing.infrastructure.cognitive_trace_repo import (
        CognitiveTraceRepository,
    )
    import fast_mcp.features.event_tracing.infrastructure.cognitive_trace_repo as repo_mod

    repo_mod._CACHE.clear()
    return {"trace_service": TraceService(CognitiveTraceRepository(FakeCG()))}


class TestTraceMessageWiring:
    """Registro en el dispatch registry del composition root."""

    def test_composition_registers_trace_message(self):
        import fast_mcp.composition  # noqa: F401 — side effect: registro
        from fast_mcp.features.event_tracing.handlers import _HANDLERS

        assert "trace_message" in _HANDLERS
        assert "trace_push" in _HANDLERS

    def test_dispatch_routes_trace_message(self):
        import fast_mcp.composition  # noqa: F401 — side effect: registro
        from fast_mcp.features.event_tracing.handlers import dispatch_trace

        out = dispatch_trace(
            "trace_message", _handler_ctx(), role="user", text="vía dispatch"
        )
        assert out.get("ok") is True

    def test_missing_service_structured_error(self):
        from fast_mcp.features.event_tracing.handlers.trace import handle_trace_message

        out = handle_trace_message({}, role="user", text="hola")
        assert out == {"error": "trace_service not wired"}


class TestPhase3ToolWiring:
    """La tool está expuesta en el catálogo fase-3 (schema + description + runner)."""

    def test_catalog_includes_trace_message(self):
        from fast_mcp.phases.phase3_tasks import (
            _DESCRIPTIONS,
            _FASE3_TOOLS,
            _INPUT_SCHEMAS,
        )

        assert "trace_message" in _FASE3_TOOLS
        assert "trace_message" in _DESCRIPTIONS
        assert "trace_message" in _INPUT_SCHEMAS
        role_schema = _INPUT_SCHEMAS["trace_message"]["properties"]["role"]
        assert role_schema.get("enum") == ["user", "agent"]
        assert _INPUT_SCHEMAS["trace_message"]["required"] == ["role", "text"]

    def test_runner_routing(self):
        from fast_mcp.phases.phase3_tasks import _build_runner

        assert callable(_build_runner("trace_message"))
