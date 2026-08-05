"""
application/presenter.py — Construcción de presentación (messages/next_actions).

Extraído de handlers.py (F4-rest — thin adapters, ADR-WALKER-016).

Encapsula el "coaching" del agente: strings 'walker X/Y' con su reason.
Los handlers orquestan negocio; este módulo construye la presentación.

Devuelve listas de NextAction (DTO de contracts) — sin lógica de negocio.
"""

from __future__ import annotations

from ..infrastructure.contracts import action


# ── Builders de acciones de coaching ──

def go_status(project_id: str = "") -> "object":
    """'walker <project>/status' — ver salud del proyecto."""
    cmd = f"walker {project_id}/status" if project_id else "walker status"
    return action(cmd, "see project health and workflow")


def go_list() -> "object":
    """'walker list' — ver proyectos registrados."""
    return action("walker list", "see all registered projects")


def go_help() -> "object":
    """'walker help' — ver protocolo y tasks."""
    return action("walker help", "see full protocol and registered tasks")


def go_routes() -> "object":
    """'walker routes' — listar endpoints REST."""
    return action("walker routes", "list all REST endpoints")


def go_prepare() -> "object":
    """'walker prepare .' — registrar el directorio actual."""
    return action("walker prepare .", "register the current directory as a project")


def go_submit(project_id: str = "", hint: str = "") -> "object":
    """'walker <project>/submit {...}' — avanzar el workflow."""
    cmd = f"walker {project_id}/submit"
    reason = "send the required fields to advance the workflow"
    if hint:
        reason = f"{reason} — {hint}"
    return action(cmd, reason)


def go_transition(project_id: str = "") -> "object":
    """'walker <project>/transition <gate>' — cruzar una gate."""
    cmd = f"walker {project_id}/transition <gate>"
    return action(cmd, "cross a workflow gate (admin/emergency)")


def go_mcp_config(fmt: str = "opencode") -> "object":
    """'walker mcp-config' — generar config MCP portátil."""
    return action(f"walker mcp-config --format={fmt}", f"config para {fmt}")


def go_capture_task(project_id: str = "") -> "object":
    """'walker <project>/capture_task {...}' — capturar spec."""
    cmd = f"walker {project_id}/capture_task" if project_id else "walker capture_task"
    return action(cmd, "capture a task spec into the active sprint")


def go_edit_plan(project_id: str = "") -> "object":
    """'walker <project>/transition edit_plan' — ajustar backlog."""
    cmd = f"walker {project_id}/transition edit_plan"
    return action(cmd, "return to planning to adjust the backlog")


def go_start_task(project_id: str = "") -> "object":
    """'walker <project>/transition start_task' — iniciar tarea."""
    cmd = f"walker {project_id}/transition start_task"
    return action(cmd, "start design + analysis of a task")


# ── Builders compuestos ──

def sprint_actions(project_id: str, sprint_active: bool = True) -> list["object"]:
    """Acciones estándar para el dashboard de un sprint."""
    actions: list[object] = []
    if sprint_active:
        actions.append(go_submit(project_id))
    actions.append(go_transition(project_id))
    actions.append(go_status(project_id))
    actions.append(go_list())
    return actions


def project_actions(project_id: str) -> list["object"]:
    """Acciones estándar para un proyecto."""
    return [go_status(project_id), go_list()]
