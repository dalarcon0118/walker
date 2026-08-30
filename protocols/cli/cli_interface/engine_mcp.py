"""engine_mcp.py — MCP module definition for the engine (workflow tools).

Moved from shared/core/engine/mcp.py to eliminate protocol coupling:
the engine (domain core) should NOT know about MCP transport.

This file lives in features/main/cli_interface/ — the adapter layer that
knows both the engine (domain) and MCP (transport).

ADR-WALKER-058: Engine protocol decoupling.
"""
from __future__ import annotations

from typing import Any

from shared.libs.module_system.contracts import ModuleDefinition, RestRoute, ToolDefinition


# ── Output schemas (SSOT, ADR-043) ───────────────────────────────────────────
# These define the JSON Schema for each tool's output envelope.

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


# ── Handler (delegates to workflow facade via closure injection) ───────────────

def _workflow_handler(name: str, workflow: Any = None):
    """Creates a handler that delegates to the workflow facade.

    The workflow facade is captured in the closure (injected by register_module).
    """
    def handler(args: dict) -> dict:
        nonlocal workflow
        if workflow is None:
            return {"error": f"Workflow tool '{name}' no disponible (sin composition root)"}

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

        # Delegate to the workflow facade
        facade_fn = getattr(workflow, name, None)
        if not facade_fn:
            return {"error": f"Unknown workflow tool: {name}"}

        try:
            if name == "status":
                result = facade_fn(project_id=project_id)
            elif name == "submit":
                result = facade_fn(chain_dir=project_id, fields=body)
            elif name == "transition":
                result = facade_fn(
                    chain_dir=project_id,
                    gate_name=body.get("gate", body.get("positional", [""])[0] if body.get("positional") else ""),
                    reason=body.get("reason", ""),
                )
            elif name == "prepare":
                result = facade_fn(project_path=args.get("path", ""), args_flags=body)
            elif name == "list":
                result = facade_fn()
            elif name == "help":
                result = facade_fn()
            else:
                return {"error": f"Unknown workflow tool: {name}"}

            if hasattr(result, "to_dict"):
                return result.to_dict()
            return result
        except Exception as exc:
            return {"error": f"Workflow tool '{name}' failed: {exc}"}

    return handler


# ── Module definition ──────────────────────────────────────────────────────────

def _build_module(workflow: Any = None) -> ModuleDefinition:
    """Builds the engine MCP module with workflow tools."""
    return ModuleDefinition(
        name="engine",
        tools=[
            ToolDefinition(
                name="status",
                description="Project workflow state + memory stats: node, sub_state, instructions, gates, CPG/CKG/Delta counts.",
                handler=_workflow_handler("status", workflow=workflow),
                input_schema={"type": "object", "properties": {}},
                output_schema=_STATUS_OUTPUT_SCHEMA,
                metadata={"tags": ["workflow", "status"]},
            ),
            ToolDefinition(
                name="submit",
                description="Submit data to current sub-state (body JSON). ADVANCES the workflow.",
                handler=_workflow_handler("submit", workflow=workflow),
                input_schema={
                    "type": "object",
                    "properties": {
                        "body": {"type": "object"},
                        "force": {"type": "boolean", "description": "Force submit if blocked by required_tools gate (uses BlockResolver force strategy)."},
                        "signal": {"type": "string", "description": "Signal name to emit on successful submit (e.g. kg_updated)."},
                    },
                },
                output_schema=_SUBMIT_OUTPUT_SCHEMA,
                metadata={"tags": ["workflow", "submit"]},
            ),
            ToolDefinition(
                name="transition",
                description="Transition between nodes via gate.",
                handler=_workflow_handler("transition", workflow=workflow),
                input_schema={
                    "type": "object",
                    "properties": {
                        "gate": {"type": "string"},
                        "reason": {"type": "string", "description": "Reason for transition (used in trace and escalation)."},
                        "force": {"type": "boolean", "description": "Force transition if blocked (uses BlockResolver force strategy)."},
                    },
                },
                output_schema=_TRANSITION_OUTPUT_SCHEMA,
                metadata={"tags": ["workflow", "transition"]},
            ),
            ToolDefinition(
                name="prepare",
                description="Initialize project at path (creates .cognitive/).",
                handler=_workflow_handler("prepare", workflow=workflow),
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
                output_schema=_PREPARE_OUTPUT_SCHEMA,
                metadata={"tags": ["workflow", "prepare"]},
            ),
            ToolDefinition(
                name="list",
                description="List projects managed by walker.",
                handler=_workflow_handler("list", workflow=workflow),
                input_schema={"type": "object", "properties": {}},
                output_schema=_LIST_OUTPUT_SCHEMA,
                metadata={"tags": ["workflow", "list"]},
            ),
            ToolDefinition(
                name="help",
                description="Walker protocol and available tasks.",
                handler=_workflow_handler("help", workflow=workflow),
                input_schema={"type": "object", "properties": {}},
                output_schema=_HELP_OUTPUT_SCHEMA,
                metadata={"tags": ["workflow", "help"]},
            ),
        ],
        routes=[
            RestRoute(path="{project_id}/status", method="GET", flags_builder=lambda a: {}),
            RestRoute(path="{project_id}/submit", method="POST", flags_builder=lambda a: {"_payload": a.get("body", {}), "force": a.get("force"), "signal": a.get("signal"), "timeout_ms": a.get("timeout_ms")}),
            RestRoute(path="{project_id}/transition", method="POST", flags_builder=lambda a: {"_payload": {"gate": a.get("gate", ""), "positional": [a.get("gate", "")]}, "force": a.get("force"), "reason": a.get("reason"), "timeout_ms": a.get("timeout_ms")}),
            RestRoute(path="prepare", method="POST", flags_builder=lambda a: {"path": a.get("path", "")}),
            RestRoute(path="list", method="GET", flags_builder=lambda a: {}),
            RestRoute(path="help", method="GET", flags_builder=lambda a: {}),
        ],
        metadata={"description": "Engine workflow module", "tags": ["workflow", "engine"]},
    )


def register_module(registry: Any, workflow: Any = None) -> None:
    """Register the engine MCP module with the ModuleRegistry.

    Args:
        registry: The ModuleRegistry to register with.
        workflow: The workflow facade (optional, for DI). If None, falls back
                  to resolve_service("workflow") in the handlers.
    """
    registry.register_module(_build_module(workflow=workflow))
