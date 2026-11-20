"""fast_mcp.tool_profiles — perfiles de exposición de tools del MCP.

Problema medido (2026-11-17): el server registra 33 tools, ~3.500 tokens de
esquemas+descripciones que el agente paga en cada request y, peor, elige entre
ellas. Varias se solapan con las tools nativas del agente (`read_code` vs
`read`, `code_search` vs `grep`) y varias son plumbing que el agente nunca debe
llamar a mano (`trace_push`, `trace_message`, `project_for_path`).

Un perfil es una lista blanca. `full` es el contrato histórico (33); `agent` es
la superficie que un agente necesita para resolver una tarea del workflow.

Se selecciona con `WALKER_MCP_PROFILE`. Default: `full` (compatibilidad).
"""

from __future__ import annotations

import logging
import os

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

#: Todo lo que el server registra. Es el default por compatibilidad.
FULL: frozenset[str] = frozenset()  # vacío = sin filtro

#: Superficie de agente. Criterio: o el agente la usa para resolver la tarea, o
#: no se expone. Fuera quedan:
#:   - duplicados (`list`, `read_code`, `code_search` — nativas del agente),
#:   - lectores de conocimiento solapados con `brief` (AE-INV-01: un lector),
#:   - mantenimiento de índices (el watcher/git los corre, no el agente),
#:   - telemetría (`trace_*`: la escribe el plugin, no el modelo).
AGENT: frozenset[str] = frozenset({
    # workflow
    "status",
    "workflow_state",
    "submit",
    "advance",
    "transition",
    # contexto del proyecto: UN lector (AE-INV-01). `brief` compone lo que
    # antes eran `agent_context` + `preflight`; las tools viejas siguen en
    # `full`/`admin` por compatibilidad del CLI, pero el agente ya no elige
    # entre tres lectores solapados.
    "brief",
    # Los templates del workflow mandan a `search_memories` en los gates de
    # análisis y cierre; dejarla fuera hacía que el agente recibiera una
    # instrucción imposible ("Unknown tool"). Es la query federada real.
    "search_memories",
    # skills: descubrir y cargar bajo demanda
    "search_skills",
    "load_skill",
    # intake y cierre: sin estas dos, la skill y el comando no pueden declarar
    # la tarea ni documentar lo decidido (se detectó al aplicar el perfil en la
    # config real: `implement_task` había quedado fuera).
    "implement_task",
    "documenting_knowledge",
    # continuidad y foco (sesión → sprint y plan de vuelo). `session_open` y
    # `session_close` NO: son ciclo de vida del plugin, no superficie del agente.
    "session_bind",
    "session_plan",
    "spec_start",
    "spec_status",
    "session_list",
    "sprint_status",
    # verificación
    "run_tests",
    "verify_predicates",
    # evidencia
    "capture",
})

#: Arranque/enrolamiento de un proyecto nuevo (lo corre una persona, no el agente).
ADMIN: frozenset[str] = AGENT | frozenset({
    "prepare",
    "list_projects",
    "project_for_path",
    "knowledge_query",
    "documenting_knowledge",
    "index_repo",
    "build_code_index",
    "update_incremental_code",
    "extract_knowledge",
    "memory_schema",
    "search_memories",
    "recall_knowledge",
    "sprint_planning",
    "resolve_project_sprint",
    "implement_task",
    "help",
})

PROFILES: dict[str, frozenset[str]] = {"full": FULL, "agent": AGENT, "admin": ADMIN}


def active_profile() -> str:
    """Nombre del perfil activo (`WALKER_MCP_PROFILE`, default `full`)."""
    name = (os.environ.get("WALKER_MCP_PROFILE") or "full").strip().lower()
    return name if name in PROFILES else "full"


def apply_profile(server: FastMCP, name: str | None = None) -> list[str]:
    """Quita del server las tools que el perfil no expone.

    Returns:
        Los nombres efectivamente removidos (vacío si el perfil es `full`).
    """
    profile = PROFILES.get((name or active_profile()), FULL)
    if not profile:
        return []

    kept = {tool.name for tool in server._tool_manager.list_tools()}  # noqa: SLF001
    dropped = sorted(kept - set(profile))
    for tool_name in dropped:
        try:
            server.remove_tool(tool_name)
        except Exception as exc:  # noqa: BLE001 - no romper el boot por una tool
            logger.warning("tool_profile: no se pudo remover %s (%s)", tool_name, exc)
    # Fail-loud si el perfil nombra tools que no existen: el perfil quedó viejo.
    missing = sorted(set(profile) - kept)
    if missing:
        logger.warning("tool_profile '%s': tools inexistentes %s", name, missing)
    logger.info(
        "tool_profile '%s': %d expuestas, %d removidas %s",
        name or active_profile(), len(kept) - len(dropped), len(dropped),
        dropped[:8],
    )
    return dropped
