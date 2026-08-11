"""mcp.py — Engine MCP protocol module.

Declares workflow tools and dynamic public tasks from task registry.
Auto-discovery via ModuleRegistry.
"""
from __future__ import annotations

from libs.mcp_server import McpServer

from features.engine.interface.contracts import ModuleDefinition, RestRoute, ToolDefinition


# ── Schemas de salida de las tools manuales del engine (SSOT, ADR-043) ──────
# El handler devuelve el envelope Response.to_dict(); `data` varía por tool.
_RESPONSE_ENVELOPE = {
    "type": "object",
    "properties": {
        "status_code": {"type": "integer"},
        "type": {"type": "string"},
        "message": {"type": "string"},
        "data": {"type": "object"},
        "errors": {"type": "array", "items": {"type": "string"}},
        "next_actions": {
            "type": "array",
            "items": {
                "properties": {"command": {"type": "string"}, "reason": {"type": "string"}},
            },
        },
    },
    "required": ["type", "status_code", "message", "data"],
}

_STATUS_OUTPUT_SCHEMA = {
    **_RESPONSE_ENVELOPE,
    "properties": {
        **_RESPONSE_ENVELOPE["properties"],
        "data": {
            "type": "object",
            "properties": {
                "sprint_id": {"type": "string"},
                "node": {"type": "string"},
                "sub_state": {"type": "string"},
                "state": {"type": "object"},
                "gates": {
                    "type": "array",
                    "items": {
                        "properties": {"name": {"type": "string"}},
                    },
                },
                "next_steps": {"type": "array"},
                "warnings": {"type": "array"},
            },
        },
    },
}

_SUBMIT_OUTPUT_SCHEMA = {
    **_RESPONSE_ENVELOPE,
    "data": {
        "type": "object",
        "properties": {
            "sub_state": {"type": "string"},
            "field_count": {"type": "integer"},
            "xor_done": {"type": "boolean"},
            "xor_next": {"type": "string"},
        },
    },
}

_TRANSITION_OUTPUT_SCHEMA = {
    **_RESPONSE_ENVELOPE,
    "data": {
        "type": "object",
        "properties": {
            "gate": {"type": "string"},
            "from_node": {"type": "string"},
            "from_sub": {"type": "string"},
            "to_node": {"type": "string"},
            "to_sub": {"type": "string"},
        },
    },
}

_PREPARE_OUTPUT_SCHEMA = {
    **_RESPONSE_ENVELOPE,
    "data": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "path": {"type": "string"},
            "type": {"type": "string", "description": "prepare_result | already_exists"},
            "message": {"type": "string"},
        },
    },
}

_LIST_OUTPUT_SCHEMA = {
    **_RESPONSE_ENVELOPE,
    "data": {
        "type": "object",
        "properties": {
            "projects": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string"},
                        "name": {"type": "string"},
                        "repo_path": {"type": "string"},
                        "active": {"type": "boolean"},
                        "active_sprints": {"type": "array"},
                    },
                },
            }
        },
    },
}

_HELP_OUTPUT_SCHEMA = {
    **_RESPONSE_ENVELOPE,
    "data": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "protocol": {"type": "string"},
            "sprint_lifecycle": {"type": "object"},
            "workflow_map": {"type": "object"},
            "commands": {"type": "array"},
            "task_catalog": {"type": "object"},
            "indices": {"type": "array"},
            "index_errors": {"type": "array"},
        },
    },
}


def register_module(registry: Any) -> None:
    module = ModuleDefinition(
        name="engine",
        tools=[
            ToolDefinition(name="status", description="Project workflow state + memory stats: node, sub_state, instructions, gates, CPG/CKG/Delta counts.", handler=_workflow_handler("status"), input_schema={"type": "object", "properties": {}}, output_schema=_STATUS_OUTPUT_SCHEMA, metadata={"tags": ["workflow", "status"]}),
            ToolDefinition(name="submit", description="Submit data to current sub-state (body JSON). ADVANCES the workflow.", handler=_workflow_handler("submit"), input_schema={"type": "object", "properties": {"body": {"type": "object"}, "force": {"type": "boolean", "description": "Force submit if blocked by required_tools gate (uses BlockResolver force strategy)."}, "signal": {"type": "string", "description": "Signal name to emit on successful submit (e.g. kg_updated)."}}}, output_schema=_SUBMIT_OUTPUT_SCHEMA, metadata={"tags": ["workflow", "submit"]}),
            ToolDefinition(name="transition", description="Transition between nodes via gate.", handler=_workflow_handler("transition"), input_schema={"type": "object", "properties": {"gate": {"type": "string"}, "reason": {"type": "string", "description": "Reason for transition (used in trace and escalation)."}, "force": {"type": "boolean", "description": "Force transition if blocked (uses BlockResolver force strategy)."}}}, output_schema=_TRANSITION_OUTPUT_SCHEMA, metadata={"tags": ["workflow", "transition"]}),
            ToolDefinition(name="prepare", description="Initialize project at path (creates .cognitive/).", handler=_workflow_handler("prepare"), input_schema={"type": "object", "properties": {"path": {"type": "string"}}}, output_schema=_PREPARE_OUTPUT_SCHEMA, metadata={"tags": ["workflow", "prepare"]}),
            ToolDefinition(name="list", description="List projects managed by walker.", handler=_workflow_handler("list"), input_schema={"type": "object", "properties": {}}, output_schema=_LIST_OUTPUT_SCHEMA, metadata={"tags": ["workflow", "list"]}),
            ToolDefinition(name="help", description="Walker protocol and available tasks.", handler=_workflow_handler("help"), input_schema={"type": "object", "properties": {}}, output_schema=_HELP_OUTPUT_SCHEMA, metadata={"tags": ["workflow", "help"]}),
        ],
        routes=[
            RestRoute(path="{project_id}/status", method="GET", flags_builder=lambda a: {}),
            RestRoute(path="{project_id}/submit", method="POST", flags_builder=lambda a: {"_payload": a.get("body", {})}),
            RestRoute(path="{project_id}/transition", method="POST", flags_builder=lambda a: {"_payload": {"gate": a.get("gate", ""), "positional": [a.get("gate", "")]}}),
            RestRoute(path="prepare", method="POST", flags_builder=lambda a: {"path": a.get("path", "")}),
            RestRoute(path="list", method="GET", flags_builder=lambda a: {}),
            RestRoute(path="help", method="GET", flags_builder=lambda a: {}),
        ],
        metadata={"description": "Engine workflow module", "tags": ["workflow", "engine"]},
    )
    registry.register_module(module)


def _workflow_handler(name: str):
    def handler(args: dict) -> dict:
        from features.cli_interface.application.handlers import (
            handle_status, handle_submit, handle_transition,
            handle_prepare, handle_list_projects, handle_help,
        )
        from features.cli_interface.infrastructure.contracts import Request

        project_id = args.get("project_id", "")

        body = args.get("body", {})
        if not isinstance(body, dict):
            body = {}

        if name == "submit":
            if "force" in args:
                body["force"] = args["force"]
            if "signal" in args:
                body["signal"] = args["signal"]
            if "timeout_ms" in args:
                body["timeout_ms"] = args["timeout_ms"]
        elif name == "transition":
            if "force" in args:
                body["force"] = args["force"]
            if "reason" in args:
                body["reason"] = args["reason"]
            if "timeout_ms" in args:
                body["timeout_ms"] = args["timeout_ms"]

        handlers_map = {
            "status": handle_status,
            "submit": handle_submit,
            "transition": handle_transition,
            "prepare": handle_prepare,
            "list": handle_list_projects,
            "help": handle_help,
        }
        handler_fn = handlers_map.get(name)
        if not handler_fn:
            return {"error": f"Unknown workflow tool: {name}"}
        
        req = Request(
            method="POST" if name in ("submit", "transition", "prepare") else "GET",
            path=f"{project_id}/{name}" if project_id else name,
            params={"projectId": project_id} if project_id else {},
            flags={"_payload": body},
        )
        result = handler_fn(req)
        if hasattr(result, "to_dict"):
            return result.to_dict()
        return result
    return handler
