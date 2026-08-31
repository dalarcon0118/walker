"""workflow_tools.py — Tasks públicas del workflow (status, submit, transition, prepare, list, help).

ADR-047 Fase 4: el TaskRegistry de main es el SSOT de las tools MCP. Estas
6 tasks del workflow migran desde los ToolDefinitions del engine a tasks
públicas con su contrato completo (description + input_schema + output_schema).

Los handlers delegan en los use cases de cli_interface (handle_*), la misma
capa que usaba el engine MCP. Los output_schemas viven en
infrastructure/workflow_schemas.py (SSOT, ADR-043) — la task queda como
adapter delgado (ADR-051 G4).

Result pattern (dry-python/returns): los handlers nunca lanzan — el wrapper
@safe del registry traduce excepciones a TaskResult.failure.
"""
from __future__ import annotations

from typing import Any

from shared.core.engine.domain.execution import TaskResult
from tasks.infrastructure.workflow_schemas import (
    HELP_OUTPUT_SCHEMA,
    LIST_OUTPUT_SCHEMA,
    PREPARE_OUTPUT_SCHEMA,
    STATUS_OUTPUT_SCHEMA,
    SUBMIT_OUTPUT_SCHEMA,
    TRANSITION_OUTPUT_SCHEMA,
)

# ── Definiciones de las 6 tools del workflow (contrato SSOT, ADR-043) ────────

_WORKFLOW_TOOLS = [
    {
        "name": "status",
        "description": "Project workflow state + memory stats: node, sub_state, instructions, gates, CPG/CKG/Delta counts.",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": STATUS_OUTPUT_SCHEMA,
        "tags": ["workflow", "status"],
    },
    {
        "name": "submit",
        "description": "Submit data to current sub-state (body JSON). ADVANCES the workflow.",
        "input_schema": {
            "type": "object",
            "properties": {
                "body": {"type": "object"},
                "force": {"type": "boolean", "description": "Force submit if blocked by required_tools gate (uses BlockResolver force strategy)."},
                "signal": {"type": "string", "description": "Signal name to emit on successful submit (e.g. kg_updated)."},
            },
        },
        "output_schema": SUBMIT_OUTPUT_SCHEMA,
        "tags": ["workflow", "submit"],
    },
    {
        "name": "transition",
        "description": "Transition between nodes via gate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "gate": {"type": "string"},
                "reason": {"type": "string", "description": "Reason for transition (used in trace and escalation)."},
                "force": {"type": "boolean", "description": "Force transition if blocked (uses BlockResolver force strategy)."},
            },
        },
        "output_schema": TRANSITION_OUTPUT_SCHEMA,
        "tags": ["workflow", "transition"],
    },
    {
        "name": "prepare",
        "description": "Initialize project at path (creates .cognitive/).",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
        "output_schema": PREPARE_OUTPUT_SCHEMA,
        "tags": ["workflow", "prepare"],
    },
    {
        "name": "list",
        "description": "List projects managed by walker.",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": LIST_OUTPUT_SCHEMA,
        "tags": ["workflow", "list"],
    },
    {
        "name": "help",
        "description": "Walker protocol and available tasks.",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": HELP_OUTPUT_SCHEMA,
        "tags": ["workflow", "help"],
    },
]


def _workflow_task(name: str):
    """Task handler del workflow: traduce (ctx, data) → task del dominio.

    Delega en los use cases de cli_interface (handle_*), construyendo el
    Request igual que lo hacía el engine MCP: el project_id viene de
    data._project_id (o project_id) con fallback al basename de chain_dir.
    """

    def handler(ctx: Any, data: dict) -> TaskResult:
        from protocols.cli.cli_interface.application.handlers import (
            handle_help,
            handle_list_projects,
            handle_prepare,
            handle_status,
            handle_submit,
            handle_transition,
        )
        from protocols.cli.cli_interface.infrastructure.contracts import Request

        args = dict(data or {})
        project_id = args.pop("_project_id", None) or args.pop("project_id", "") or ""
        if not project_id:
            chain = str(getattr(ctx, "chain_dir", "") or "")
            project_id = chain.rstrip("/").split("/")[-1] if chain else ""

        body = args.get("_payload")
        if not isinstance(body, dict):
            body = args.get("body", {}) if isinstance(args.get("body", {}), dict) else {}
        for extra in ("force", "signal", "timeout_ms", "reason"):
            if extra in args:
                body[extra] = args[extra]

        handlers_map = {
            "status": handle_status,
            "submit": handle_submit,
            "transition": handle_transition,
            "prepare": handle_prepare,
            "list": handle_list_projects,
            "help": handle_help,
        }
        handler_fn = handlers_map[name]
        req = Request(
            method="POST" if name in ("submit", "transition", "prepare") else "GET",
            path=f"{project_id}/{name}" if project_id else name,
            params={"projectId": project_id} if project_id else {},
            flags={"_payload": body},
        )
        result = handler_fn(req)
        if hasattr(result, "to_dict"):
            return TaskResult.success(data=result.to_dict())
        if isinstance(result, dict):
            return TaskResult.success(data=result)
        return TaskResult.success(data={"type": name, "data": {"result": result}})

    return handler


def register(registry: Any) -> None:
    """Registra las 6 tasks públicas del workflow en el TaskRegistry.

    ADR-047 Fase 4: metadata scope="public" para que el catálogo de tools
    MCP (tool_catalog.list_tools, SSOT TaskRegistry) las exponga con su
    contrato completo (input/output schema, ADR-043).
    """
    for tool in _WORKFLOW_TOOLS:
        name = tool["name"]
        registry.register(
            name,
            _workflow_task(name),
            metadata={
                "description": tool["description"],
                "input_schema": tool["input_schema"],
                "output_schema": tool["output_schema"],
                "usage": f"walker <project>/{name}",
                "tags": tool["tags"],
                "scope": "public",
            },
        )
