"""task_execution/infrastructure/workflow_schemas.py — Schemas de las tools del workflow.

Contratos de salida de las 6 tools manuales del workflow (SSOT, ADR-043).
Migrados desde core/engine/mcp.py: el handler devuelve Response.to_dict();
`data` varía por tool. Extraídos de workflow_tools.py (ADR-051 G4) para que
la task quede como adapter delgado y el contrato viva en infraestructura.
"""
from __future__ import annotations

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

STATUS_OUTPUT_SCHEMA = {
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

SUBMIT_OUTPUT_SCHEMA = {
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

TRANSITION_OUTPUT_SCHEMA = {
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

PREPARE_OUTPUT_SCHEMA = {
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

LIST_OUTPUT_SCHEMA = {
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

HELP_OUTPUT_SCHEMA = {
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
