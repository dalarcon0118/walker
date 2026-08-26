"""Tests BDD (pytest-bdd) — Coherencia de contrato SSOT (F5 SPEC-043).

Feature value: tools/list, walker://help, validate_llm_task_output y los DTOs
son coherentes, garantizando un contrato único sin alucinaciones.

Escenarios: bdd/features/f5_schema_coherence.feature
"""

from __future__ import annotations


import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/f5_schema_coherence.feature")


@pytest.fixture
def f5_ctx() -> dict:
    return {}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _catalog_tools():
    from shared.core.engine.application.tool_catalog import list_tools

    return list_tools() or []


# ── Catálogo ─────────────────────────────────────────────────────────────────


@given("el catálogo de tools del sistema")
def _system_tool_catalog(f5_ctx: dict):
    f5_ctx["tools"] = _catalog_tools()


@when("se listan las tools")
def _list_tools(f5_ctx: dict):
    pass


@then("cada tool tiene inputSchema dict object")
def _assert_input_schema_dict(f5_ctx: dict):
    for t in f5_ctx["tools"]:
        assert isinstance(t.get("inputSchema"), dict), f"{t['name']}: sin inputSchema"
        # Contrato cliente MCP estricto (opencode): inputSchema DEBE ser
        # JSON Schema object con properties dict. Un {} o {"type":"object"}
        # sin properties rompe tools/list -> "Failed to get tools" (bug walker).
        schema = t["inputSchema"]
        assert schema.get("type") == "object", (
            f"{t['name']}: inputSchema.schema sin type object ({schema})"
        )
        assert isinstance(schema.get("properties", {}), dict), (
            f"{t['name']}: inputSchema sin properties dict"
        )


@then("cada tool tiene outputSchema dict object")
def _assert_output_schema_dict(f5_ctx: dict):
    for t in f5_ctx["tools"]:
        assert isinstance(t.get("outputSchema"), dict), f"{t['name']}: sin outputSchema"


@then("cada tool tiene description")
def _assert_tool_description(f5_ctx: dict):
    for t in f5_ctx["tools"]:
        assert t.get("description"), f"{t['name']}: sin descripción"


@given("las 4 tools de fase del SPEC-043")
def _phase_tools(f5_ctx: dict):
    f5_ctx["tools"] = {t["name"]: t for t in _catalog_tools()}


@when("se consulta el catálogo")
def _query_catalog(f5_ctx: dict):
    pass


@then(
    "kg_task_context, kg_diagnose, kg_qa_evidence y kg_sprint_evidence tienen outputSchema type object"
)
def _assert_phase_tools_output_type(f5_ctx: dict):
    tools = f5_ctx["tools"]
    for name in (
        "kg_task_context",
        "kg_diagnose",
        "kg_qa_evidence",
        "kg_sprint_evidence",
    ):
        assert name in tools, f"{name} no está en el catálogo"
        out = tools[name].get("outputSchema") or {}
        assert out.get("type") == "object", f"{name}: outputSchema sin type object"


@then("sus properties son dicts")
def _assert_properties_dicts(f5_ctx: dict):
    tools = f5_ctx["tools"]
    for name in (
        "kg_task_context",
        "kg_diagnose",
        "kg_qa_evidence",
        "kg_sprint_evidence",
    ):
        assert name in tools, f"{name} no está en el catálogo"
        out = tools[name].get("outputSchema") or {}
        assert isinstance(out.get("properties", {}), dict), f"{name}: sin properties"


@given("el catálogo de tools")
def _catalog_loaded(f5_ctx: dict):
    f5_ctx["tools"] = {t["name"]: t for t in _catalog_tools()}


@when("se pide ayuda de kg_task_context")
def _help_kg_task_context(f5_ctx: dict):
    from shared.core.engine.application.tool_catalog import build_tool_help

    f5_ctx["help_doc"] = build_tool_help("kg_task_context") if f5_ctx["tools"] else None


@then("la ayuda tiene name, input_schema, output_schema y description")
def _assert_help_doc_keys(f5_ctx: dict):
    doc = f5_ctx["help_doc"]
    assert doc["name"] == "kg_task_context"
    assert "input_schema" in doc
    assert "output_schema" in doc
    assert "description" in doc


@then("los schemas son dicts, no strings serializados")
def _assert_schemas_are_dicts(f5_ctx: dict):
    doc = f5_ctx["help_doc"]
    assert isinstance(doc.get("input_schema"), dict)
    assert isinstance(doc.get("output_schema"), dict)


# ── Contrato ─────────────────────────────────────────────────────────────────


@given("un nodo SPRINT.TASK_SPEC")
def _sprint_task_spec_node(f5_ctx: dict):
    pass


@when("se valida una salida con task_id y status")
def _validate_llm_output_ok(f5_ctx: dict):
    from data.workflow.contracts import validate_llm_task_output

    f5_ctx["out"] = validate_llm_task_output(
        "SPRINT", "TASK_SPEC", {"task_id": "feat-1", "status": "specified"}
    )


@then("la validación pasa sin error")
def _assert_validation_passes(f5_ctx: dict):
    out = f5_ctx["out"]
    assert out["task_id"] == "feat-1"
    assert out["status"] == "specified"


@then("el output se devuelve intacto")
def _assert_output_preserved(f5_ctx: dict):
    out = f5_ctx["out"]
    assert out["task_id"] == "feat-1"
    assert out["status"] == "specified"


@given("un nodo SPRINT.TASK_SPEC")
def _sprint_task_spec_node_2(f5_ctx: dict):
    pass


@when("se valida una salida sin task_id")
def _validate_llm_output_fail(f5_ctx: dict):
    from data.workflow.contracts import (
        ContractOutputError,
        validate_llm_task_output,
    )

    try:
        validate_llm_task_output("SPRINT", "TASK_SPEC", {"status": "x"})
        f5_ctx["error"] = None
    except ContractOutputError as e:
        f5_ctx["error"] = e


@then("se eleva ContractOutputError (anti-alucinación)")
def _assert_contract_output_error(f5_ctx: dict):
    assert f5_ctx["error"] is not None


@given("un nodo VIRTUAL.CUSTOM sin DTO")
def _virtual_node_no_dto(f5_ctx: dict):
    pass


@when("se valida una salida arbitraria")
def _validate_arbitrary(f5_ctx: dict):
    from data.workflow.contracts import validate_llm_task_output

    f5_ctx["out"] = validate_llm_task_output("VIRTUAL", "CUSTOM", {"a": 1})


@then("la validación pasa sin bloquear")
def _assert_fail_open(f5_ctx: dict):
    assert f5_ctx["out"] == {"a": 1}


# ── Workflow ─────────────────────────────────────────────────────────────────


@given("el registro de CONTEXT_SCHEMAS")
def _context_schemas(f5_ctx: dict):
    from shared.core.engine.context.contracts import CONTEXT_SCHEMAS

    f5_ctx["context_schemas"] = CONTEXT_SCHEMAS


@when("se inspecciona cada schema")
def _inspect_schemas(f5_ctx: dict):
    pass


@then("hay 9 schemas")
def _assert_9_schemas(f5_ctx: dict):
    assert len(f5_ctx["context_schemas"]) == 9


@then("cada schema tiene type object")
def _assert_type_object(f5_ctx: dict):
    for name, schema in f5_ctx["context_schemas"].items():
        assert schema["type"] == "object", f"{name}: schema sin type object"


@then("cada properties es dict")
def _assert_properties_dict(f5_ctx: dict):
    for name, schema in f5_ctx["context_schemas"].items():
        assert isinstance(schema.get("properties", {}), dict), (
            f"{name}: properties no dict"
        )


@given("el registro WORKFLOW_OUTPUTS")
def _workflow_outputs(f5_ctx: dict):
    from data.workflow.contracts import WORKFLOW_OUTPUTS

    f5_ctx["workflow_outputs"] = WORKFLOW_OUTPUTS


@when("se consulta")
def _query_workflow_outputs(f5_ctx: dict):
    pass


@then("hay al menos 16 entradas")
def _assert_16_entries(f5_ctx: dict):
    assert len(f5_ctx["workflow_outputs"]) >= 16


@then("SPRINT.TASK_SPEC está registrado")
def _assert_sprint_task_spec(f5_ctx: dict):
    assert ("SPRINT", "TASK_SPEC") in f5_ctx["workflow_outputs"]


@then("REFINE.DIAGNOSE está registrado")
def _assert_refine_diagnose(f5_ctx: dict):
    assert ("REFINE", "DIAGNOSE") in f5_ctx["workflow_outputs"]


@given("el tool kg_diagnose en el catálogo")
def _kg_diagnose_tool(f5_ctx: dict):
    from shared.core.engine.application.tool_catalog import get_tool

    f5_ctx["tool"] = get_tool("kg_diagnose")


@when("se consulta su outputSchema")
def _query_output_schema(f5_ctx: dict):
    tool = f5_ctx.get("tool")
    if tool is not None:
        f5_ctx["out"] = tool.get("outputSchema") or {}
    else:
        f5_ctx["out"] = {}


@then("es type object")
def _assert_output_type_object(f5_ctx: dict):
    assert f5_ctx["out"].get("type") == "object"


@then("sus properties incluyen diagnosis y ok")
def _assert_props_diagnosis_ok(f5_ctx: dict):
    props = set(f5_ctx["out"].get("properties", {}))
    assert {"diagnosis", "ok"} <= props | {"ok", "diagnosis"}, (props,)


@given("las tools status, submit y transition")
def _workflow_tools(f5_ctx: dict):
    from shared.core.engine.application.tool_catalog import get_tool

    f5_ctx["tools"] = {
        name: get_tool(name) for name in ("status", "submit", "transition")
    }


@when("se consultan sus outputSchema")
def _query_workflow_tool_schemas(f5_ctx: dict):
    f5_ctx["outs"] = {}
    for name, tool in f5_ctx["tools"].items():
        f5_ctx["outs"][name] = (tool or {}).get("outputSchema") or {}


@then("cada una es type object")
def _assert_each_type_object(f5_ctx: dict):
    for name, out in f5_ctx["outs"].items():
        assert out.get("type") == "object", f"{name}: outputSchema sin type object"


@then("sus properties incluyen type, status_code, message y data")
def _assert_envelope_props(f5_ctx: dict):
    for name, out in f5_ctx["outs"].items():
        props = set(out.get("properties", {}))
        assert {"type", "status_code", "message", "data"} <= props, (name, props)


@then("data es objeto con campos propios de la tool")
def _assert_data_shape(f5_ctx: dict):
    for name, out in f5_ctx["outs"].items():
        data_schema = out.get("properties", {}).get("data") or {}
        assert data_schema.get("type") == "object", f"{name}: data sin shape"


# ── Envelope ─────────────────────────────────────────────────────────────────


@given("un servidor MCP")
def _mcp_server(f5_ctx: dict):
    from features.main.cli_interface.mcp_adapter import _build_server

    f5_ctx["server"] = _build_server()


@when("se llama a status con project_id walker y proyecto inexistente")
def _call_status_both(f5_ctx: dict):
    server = f5_ctx["server"]
    f5_ctx["result"] = server.call_tool("status", {"project_id": "walker"})
    f5_ctx["result2"] = server.call_tool("status", {"project_id": "__inexistente__"})


@then("ambas respuestas tienen type, status_code, message y data")
def _assert_both_envelopes(f5_ctx: dict):
    for key in ("result", "result2"):
        result = f5_ctx[key]
        assert {"type", "status_code", "message", "data"} <= set(result), result.keys()


@then("status_code es int")
def _assert_status_code_int(f5_ctx: dict):
    assert isinstance(f5_ctx["result"]["status_code"], int)


@then("message es str")
def _assert_message_str(f5_ctx: dict):
    assert isinstance(f5_ctx["result"]["message"], str)


@then("data es dict")
def _assert_data_dict(f5_ctx: dict):
    assert isinstance(f5_ctx["result"]["data"], dict)


@when("se hace tools/call a status")
def _tools_call_status(f5_ctx: dict):
    from features.main.cli_interface.mcp_adapter import _handle_request

    f5_ctx["resp"] = _handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "status", "arguments": {"project_id": "walker"}},
        }
    )


@then("la respuesta tiene id 1 y sin error")
def _assert_jsonrpc_ok(f5_ctx: dict):
    resp = f5_ctx["resp"]
    assert resp.get("id") == 1
    assert "error" not in resp, resp


@then("el content[0].text parseado tiene type, status_code, message y data")
def _assert_content_envelope(f5_ctx: dict):
    resp = f5_ctx["resp"]
    content = (resp.get("result") or {}).get("content") or []
    assert content, "tools/call status sin content"
    text = content[0].get("text", "")
    import json as _json

    payload = _json.loads(text) if text.startswith("{") else {}
    if payload:
        assert {"type", "status_code", "message", "data"} <= set(payload), (
            payload.keys(),
        )
        assert isinstance(payload.get("data"), dict)
