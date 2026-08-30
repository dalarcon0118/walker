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


def go_mcp_config() -> "object":
    """'walker mcp-config' — generar config MCP portátil (formato único estándar)."""
    return action("walker mcp-config", "config MCP portable (formato único estándar)")


def project_actions(project_id: str) -> list["object"]:
    """Acciones estándar para un proyecto."""
    return [go_status(project_id), go_list()]
