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

ESTADO: completa — las 12 tools registran y despachan vía runners del
composition root (fast_mcp.composition): session (help, list_projects),
delta (sprint_planning, resolve_project_sprint — repo-backed, INV-016),
trace (trace_push, trace_message), task (implement_task, run_tests, verify_predicates,
capture) y workflow (submit, transition → WorkflowContext → engine con
task registry, ADR-071 Sprint 5).
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from fast_mcp.registration import register_domain_tool

logger = logging.getLogger(__name__)

# ── Tools ──────────────────────────────────────────────────────────────

_FASE3_TOOLS = (
    "submit",
    "advance",
    "transition",
    "help",
    "list_projects",
    "sprint_planning",
    "resolve_project_sprint",
    "workflow_state",
    "project_for_path",
    "trace_push",
    "trace_message",
    "implement_task",
    "session_bind",
    "session_open",
    "session_close",
    "session_list",
    "session_plan",
    "spec_start",
    "sprint_status",
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
    "advance": {
        "type": "object",
        "properties": {
            "fields": {"type": "object", "description": "Fields for the current sub-state"},
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
            "spring_id": {"type": "string", "description": "Sprint identifier (default sprint-<project>)"},
            "status": {
                "type": "string",
                "description": "planning|active (default active). Un roadmap nace en planning",
            },
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
            "session_id": {
                "type": "string",
                "description": "Session whose sprint to resolve first (then active, then planning)",
            },
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
    "trace_message": {
        "type": "object",
        "properties": {
            "role": {
                "type": "string",
                "description": "Message author: 'user' or 'agent'",
                "enum": ["user", "agent"],
            },
            "text": {"type": "string", "description": "Message content (required)"},
            "session_id": {"type": "string", "description": "Session identifier"},
            "task_id": {"type": "string", "description": "Task identifier"},
            "sprint_id": {"type": "string", "description": "Sprint identifier"},
            "source": {"type": "string", "description": "Event source (default: walker)"},
            "project_id": {"type": "string", "description": "Project identifier (resolves chain_dir)"},
        },
        "required": ["role", "text"],
    },
    "session_bind": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Session identifier (el id real de la sesión)"},
            "mode": {
                "type": "string",
                "description": "auto = solo leer; new = crear sprint de la sesión; continue = sumar la sesión al sprint previo",
                "enum": ["auto", "new", "continue"],
            },
            "objective": {"type": "string", "description": "Primer pedido de la sesión (goal del sprint)"},
            "parent_session_id": {"type": "string", "description": "Sesión previa si esta continúa un goal"},
            "spring_id": {
                "type": "string",
                "description": "Sprint existente al que unirse (p. ej. el sprint del roadmap). Default: el de la sesión o el activo",
            },
            "project_id": {"type": "string", "description": "Project identifier"},
        },
        "required": ["session_id"],
    },
    "session_open": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Session identifier"},
            "project_id": {"type": "string", "description": "Project identifier"},
            "agent": {"type": "string", "description": "Agent name"},
            "model": {"type": "string", "description": "Model used by the session"},
            "parent_session_id": {"type": "string", "description": "Sesión previa (jerarquía)"},
            "spring_id": {"type": "string", "description": "Sprint ligado"},
            "goal": {"type": "string", "description": "Goal de la sesión"},
        },
        "required": ["session_id"],
    },
    "session_close": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Session identifier"},
            "outcome": {"type": "string", "description": "Resultado (succeeded|failed|interrupted|...)"},
        },
        "required": ["session_id"],
    },
    "session_list": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "Project identifier"},
            "status": {"type": "string", "description": "active|closed"},
            "limit": {"type": "integer", "description": "Máximo de sesiones"},
        },
    },
    "session_plan": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Session identifier"},
            "mode": {"type": "string", "description": "get|set", "enum": ["get", "set"]},
            "goal": {"type": "string", "description": "Destino de la sesión"},
            "acceptance": {
                "type": "array",
                "description": "Criterios de aceptación verificables",
                "items": {"type": "string"},
            },
            "task": {"type": "string", "description": "Tarea actual"},
            "scope": {
                "type": "array",
                "description": "Archivos en alcance",
                "items": {"type": "string"},
            },
            "next": {"type": "string", "description": "Próximo paso concreto"},
            "project": {
                "type": "string",
                "description": "Proyecto de TRABAJO (puede diferir del cwd de la sesión)",
            },
        },
        "required": ["session_id"],
    },
    "spec_start": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Session identifier"},
            "spring_id": {"type": "string", "description": "Sprint de la spec (o se resuelve por sesión)"},
            "spec_id": {"type": "string", "description": "Spec a iniciar (ej. SPEC-001)"},
            "task_id": {"type": "string", "description": "Task id (default: spec_id)"},
            "files": {
                "type": "array",
                "description": "Alcance (default: files de la spec)",
                "items": {"type": "string"},
            },
        },
        "required": ["spec_id"],
    },
    "sprint_status": {
        "type": "object",
        "properties": {
            "spring_id": {"type": "string", "description": "Sprint identifier"},
            "session_id": {"type": "string", "description": "Resuelve el sprint de la sesión"},
            "status": {
                "type": "string",
                "description": "planning|active|paused|reviewing|closed (paused es reversible)",
            },
        },
        "required": ["status"],
    },
    "implement_task": {
        "type": "object",
        "properties": {
            "objective": {"type": "string", "description": "Expected result of the task"},
            "files": {"type": "array", "description": "Files to modify", "items": {"type": "string"}},
            "task_id": {"type": "string", "description": "Task identifier"},
            "session_id": {
                "type": "string",
                "description": "Session identifier (atribuye la tarea al sprint de esa sesión)",
            },
            "resume": {
                "type": "boolean",
                "description": "Reanuda el sprint si está PAUSADO (default: rechazar)",
            },
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
    "project_for_path": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directorio de la sesión"},
        },
    },
    "workflow_state": {
        "type": "object",
        "properties": {
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
    "advance": "Advance to the next sub-state inside the current node (runs `auto` actions).",
    "transition": "Transition to the next workflow node via a gate.",
    "help": "Get help: available tools and current workflow state.",
    "list_projects": "List projects managed by walker.",
    "sprint_planning": "Create a sprint with specs from task list.",
    "resolve_project_sprint": "Resolve the sprint for a project/session: session > active > planning.",
    "session_bind": "Bind a chat session to a sprint (delta-sprint): auto|new|continue.",
    "session_open": "Register/open a chat session (idempotente).",
    "session_close": "Close a chat session.",
    "session_list": "List chat sessions with lifecycle and hierarchy.",
    "session_plan": "Get/set the session flight plan (goal, acceptance, task, scope, next).",
    "spec_start": "Start an existing spec: status in_progress + session/task link + plan.",
    "sprint_status": "Transition a sprint status (paused is reversible: active<->paused).",
    "trace_push": "Push an event to the execution trace index.",
    "trace_message": "Record a user or agent message in the execution trace (role: user|agent).",
    "implement_task": "Prepare task implementation (records objective and files).",
    "run_tests": "Run test suite via pytest (fail-loud if 0 tests).",
    "verify_predicates": "Verify that predicates are satisfied (file_exists, test_passes).",
    "workflow_state": "Current workflow position, allowed gates and required fields (read-only).",
    "project_for_path": "Resolve {project_id, repo_path} from a directory (registry lookup).",
    "capture": "Capture evidence and artifacts for a task.",
}


def install(server: FastMCP) -> None:
    """Register Phase 3 tools (workflow engine-backed).

    Tools: submit, transition, help, list_projects, sprint_planning,
    resolve_project_sprint, trace_push, implement_task, run_tests,
    verify_predicates, capture.
    Each tool dispatches via its composition-root runner (session, delta,
    trace, task or workflow) — inyección desde el composition root (R1).
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
    _SESSION_TOOLS = {"help", "list_projects", "project_for_path"}
    _DELTA_TOOLS = {"sprint_planning", "resolve_project_sprint"}
    _TRACE_TOOLS = {"trace_push", "trace_message"}
    _TASK_TOOLS = {"implement_task", "session_bind", "session_open", "session_close", "session_list", "session_plan", "spec_start", "sprint_status", "run_tests", "verify_predicates", "capture"}
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
