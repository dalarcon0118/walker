"""fast_mcp.phases.phase3_tasks — Fase 3: tasks de ejecución + workflow completo.

Cubre las tools de EJECUCIÓN y el flujo de trabajo completo que cierran la
migración:
  - workflow:   submit, transition, help
  - proyecto:   list_projects, detect_project_context*, build_project_config*,
                create_first_sprint*
  - planning:   sprint_planning, resolve_project_sprint
  - ejecución:  implement_task, run_tests, verify_predicates, review_code,
                capture, refine_*, research_topic, trace_push, intelligence
  - integración: update_project, context_gathering

* scope internal — no expuestas al agente, pero forman parte del bootstrap.

Tras esta fase, `fast_mcp/` cubre la superficie completa de `mcp/` y el resto
del monorepo no migrado se elimina (ADR-WALKER-071).

Capa de test de la fase: tests/ — test_workflow_e2e_*, test_task_contract_ssot,
test_smoke_agent_harness, test_agent_e2e_bdd, test_intelligence_service_bdd.

ESTADO: skeleton — solo `help` y `list_projects` implementados (proof of concept).
Los tools que dependen del engine (submit, transition, implement_task, etc.)
requieren port de `shared/core/engine` (~16k LOC) — Fase 3 completa.
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from fast_mcp.registration import register_domain_tool

logger = logging.getLogger(__name__)

# ── Tools ──────────────────────────────────────────────────────────────

_FASE3_TOOLS = (
    "submit",
    "transition",
    "help",
    "list_projects",
    "sprint_planning",
    "resolve_project_sprint",
    "trace_push",
    "implement_task",
    "run_tests",
    "verify_predicates",
    "capture",
)

# inputSchema SSOT (R-03bis)
_INPUT_SCHEMAS: dict[str, dict] = {
    "submit": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "description": "Decision to record"},
            "evidence": {"type": "string", "description": "Evidence for the decision"},
            "_payload": {"type": "object", "description": "Additional fields to submit"},
        },
    },
    "transition": {
        "type": "object",
        "properties": {
            "gate": {"type": "string", "description": "Gate name to transition via"},
            "reason": {"type": "string", "description": "Reason for transition"},
        },
        "required": ["gate"],
    },
    "help": {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "description": "Tool name to get help for (optional)."},
        },
    },
    "list_projects": {
        "type": "object",
        "properties": {},
    },
    "sprint_planning": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "Project identifier"},
            "goal": {"type": "string", "description": "Sprint goal description"},
            "tasks": {
                "type": "array",
                "description": "List of tasks (objective, type, domain)",
                "items": {"type": "object"},
            },
        },
        "required": ["goal"],
    },
    "resolve_project_sprint": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "Project identifier"},
            "chain_dir": {"type": "string", "description": "Optional chain_dir override"},
        },
    },
    "trace_push": {
        "type": "object",
        "properties": {
            "event_type": {"type": "string", "description": "Event type (e.g. task_execute, tool_call, error)"},
            "source": {"type": "string", "description": "Event source (e.g. walker_task, workflow)"},
            "task_id": {"type": "string", "description": "Task identifier"},
            "sprint_id": {"type": "string", "description": "Sprint identifier"},
            "node": {"type": "string", "description": "Workflow node"},
            "tool": {"type": "string", "description": "Tool used"},
            "data": {"type": "object", "description": "Event payload"},
            "files": {"type": "array", "description": "Affected files", "items": {"type": "string"}},
            "project_id": {"type": "string", "description": "Project identifier (resolves chain_dir)"},
        },
        "required": ["event_type"],
    },
    "implement_task": {
        "type": "object",
        "properties": {
            "objective": {"type": "string", "description": "Expected result of the task"},
            "files": {"type": "array", "description": "Files to modify", "items": {"type": "string"}},
            "task_id": {"type": "string", "description": "Task identifier"},
            "project_id": {"type": "string", "description": "Project identifier"},
        },
        "required": ["objective"],
    },
    "run_tests": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task identifier"},
            "project_id": {"type": "string", "description": "Project identifier"},
        },
    },
    "verify_predicates": {
        "type": "object",
        "properties": {
            "predicates": {
                "type": "array",
                "description": "List of predicates to verify (type + target)",
                "items": {"type": "object"},
            },
            "task_id": {"type": "string", "description": "Task identifier"},
            "project_id": {"type": "string", "description": "Project identifier"},
        },
    },
    "capture": {
        "type": "object",
        "properties": {
            "evidence": {"type": "string", "description": "Evidence text to capture"},
            "artifacts": {"type": "array", "description": "Artifact file paths", "items": {"type": "string"}},
            "task_id": {"type": "string", "description": "Task identifier"},
            "project_id": {"type": "string", "description": "Project identifier"},
        },
    },
}

_DESCRIPTIONS = {
    "submit": "Submit data to the current workflow sub-state.",
    "transition": "Transition to the next workflow node via a gate.",
    "help": "Get help: available tools and current workflow state.",
    "list_projects": "List projects managed by walker.",
    "sprint_planning": "Create a sprint with specs from task list.",
    "resolve_project_sprint": "Find the active sprint for a project.",
    "trace_push": "Push an event to the execution trace index.",
    "implement_task": "Prepare task implementation (records objective and files).",
    "run_tests": "Run test suite via pytest (fail-loud if 0 tests).",
    "verify_predicates": "Verify that predicates are satisfied (file_exists, test_passes).",
    "capture": "Capture evidence and artifacts for a task.",
}


def install(server: FastMCP) -> None:
    """Register Phase 3 tools (workflow engine-backed).

    Tools: submit, transition, status, help, list_projects.
    Each tool dispatches via workflow_tool_runner → WorkflowContext → handlers.
    """
    for name in _FASE3_TOOLS:
        runner = _build_runner(name)
        register_domain_tool(
            server,
            name=name,
            runner=runner,
            description=_DESCRIPTIONS[name],
            input_schema=_INPUT_SCHEMAS[name],
        )
    logger.info("fast_mcp fase3: %s registered", ", ".join(_FASE3_TOOLS))


def _build_runner(name: str):
    """Build a tool runner via session, workflow, delta, trace, or task runner."""
    _SESSION_TOOLS = {"help", "list_projects"}
    _DELTA_TOOLS = {"sprint_planning", "resolve_project_sprint"}
    _TRACE_TOOLS = {"trace_push"}
    _TASK_TOOLS = {"implement_task", "run_tests", "verify_predicates", "capture"}
    if name in _SESSION_TOOLS:
        from fast_mcp.composition import session_tool_runner
        return session_tool_runner(name)
    if name in _DELTA_TOOLS:
        from fast_mcp.composition import delta_tool_runner
        return delta_tool_runner(name)
    if name in _TRACE_TOOLS:
        from fast_mcp.composition import trace_tool_runner
        return trace_tool_runner(name)
    if name in _TASK_TOOLS:
        from fast_mcp.composition import task_tool_runner
        return task_tool_runner(name)
    from fast_mcp.composition import workflow_tool_runner
    return workflow_tool_runner(name)
