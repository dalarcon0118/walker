"""cli/router.py — Routing y dispatch principal del CLI.

Responsabilidad:
  - main() es la entrada principal (llamada desde walker_v3.py)
  - _resolve_path() descompone la ruta en [target, args...]
  - _dispatch() mapea target → función cmd_*
  - COMMANDS es el dict de mapeo para comandos sin prefijo
"""
from __future__ import annotations
import os
import sys
from typing import Callable

from bootstrap.cli.commands import (
    cmd_list,
    cmd_status,
    cmd_submit,
    cmd_advance,
    cmd_transition,
    cmd_session,
    cmd_config,
    cmd_workflow,
    cmd_task,
    cmd_tasks,
    cmd_switch,
    cmd_help,
    cmd_prepare,
)
from bootstrap.cli.tracing import cmd_register_file, cmd_register_message, cmd_trace_log


# ─── Mapeo de comandos (sin prefijo) ──────────────────────────────────────────
# Formato: nombre → (función, min_args, max_args)
# min/max_args se refieren a los args DESPUÉS del comando
COMMANDS: dict[str, tuple[Callable, int, int]] = {
    "prepare": (cmd_prepare, 0, 3),
    "list": (cmd_list, 0, 1),
    "status": (cmd_status, 0, 2),
    "submit": (cmd_submit, 2, 99),
    "advance": (cmd_advance, 2, 2),
    "transition": (cmd_transition, 3, 99),
    "session": (cmd_session, 0, 0),
    "config": (cmd_config, 0, 0),
    "workflow": (cmd_workflow, 0, 2),
    "task": (cmd_task, 2, 99),
    "tasks": (cmd_tasks, 0, 1),
    "switch": (cmd_switch, 1, 1),
    "help": (cmd_help, 0, 0),
    "register_file": (cmd_register_file, 2, 4),
    "register_message": (cmd_register_message, 3, 3),
    "trace_log": (cmd_trace_log, 3, 99),
}


def _resolve_path(path: str) -> tuple[str, list[str]]:
    """Descompone una ruta estilo CLI en target y args.

    "list"              → ("list", [])
    "status"            → ("status", [])
    "list/project1"     → ("list", ["project1"])
    "submit/proj/sp"    → ("submit", ["proj", "sp"])
    "transition/a/b/c"  → ("transition", ["a", "b", "c"])
    "task/demo/create_first_sprint" → ("task", ["demo", "create_first_sprint"])

    Si la ruta no contiene '/', target = ruta completa.
    """
    parts = path.strip("/").split("/")
    target = parts[0]
    args = parts[1:]
    return target, args


def _handle_agents_path(agent_id: str, sub_args: list[str]) -> int:
    """Maneja una ruta agents/<id>/... ya resuelta.

    agent_id: el ID del agente
    sub_args: todo lo que sigue a agents/<id>/ (e.g. ['delta-skill', 'status'])
    """
    os.environ["WALKER_AGENT_ID"] = agent_id

    if not sub_args:
        # Solo "agents/<id>" → dashboard
        from bootstrap.cli.project import _list_projects, _show_projects_dashboard
        projects = _list_projects()
        return _show_projects_dashboard(projects)

    # Re-ensamblar sub_args como si fuera el argv completo
    return _dispatch(sub_args)


def _try_agents_path(raw: str, extra_rest: list[str] | None = None) -> int | None:
    """Si raw empieza con 'agents/', extrae el id y despacha.

    Combina los args extraídos del path con extra_rest (desde _dispatch)
    para que 'agents/<id>/switch delta-skill' funcione correctamente.

    Retorna exit_code si coincide, None si no.
    """
    if raw.startswith("agents/"):
        parts = raw.split("/")
        # parts = ['agents', '<id>', ...]
        if len(parts) >= 2:
            agent_id = parts[1]
            sub_args = parts[2:]
            if extra_rest:
                sub_args = sub_args + extra_rest
            return _handle_agents_path(agent_id, sub_args)
    return None


def _resolve_project_command(raw: str) -> tuple[str, list[str]] | None:
    """Si raw contiene un comando conocido como último segmento,
    retorna (comando, args_con_proyecto).

    "delta-skill/workflow"   → ("workflow", ["delta-skill"])
    "delta-skill/status"     → ("status", ["delta-skill"])
    "demo/task/kg_search"    → ("task", ["demo", "kg_search"])
    "proj/sprint-1/status"   → ("status", ["proj", "sprint-1"])
    "proj/sprint-1/submit"   → ("submit", ["proj", "sprint-1"])
    "simple-command"          → None
    """
    parts = raw.strip("/").split("/")
    # Buscar de derecha a izquierda: el segmento más a la derecha
    # que sea un comando conocido
    for i in range(len(parts) - 1, -1, -1):
        candidate = parts[i]
        if candidate in COMMANDS:
            # Comando encontrado en posición i
            # args = todo lo que está antes de i (proyecto/ruta)
            cmd_args = parts[:i]
            # Si hay resto después del comando (task/nombre), incluirlo
            if i + 1 < len(parts):
                cmd_args.extend(parts[i + 1:])
            return candidate, cmd_args
    return None


def _find_command_in_args(args: list[str]) -> tuple[str, list[str]] | None:
    """Busca un comando conocido en la lista de args.

    Retorna (comando, args_para_ese_comando) o None.
    Incluye args ANTES y DESPUÉS del comando en la lista de args.

    "status"              → ("status", [])
    "delta-skill" "status" → ("status", ["delta-skill"])
    "delta-skill" "sprint-1" "status" → ("status", ["delta-skill", "sprint-1"])
    "list"                → ("list", [])
    "help"                → ("help", [])
    "delta-skill" "task" "query" "--query" "{}" → ("task", ["delta-skill", "query", "--query", "{}"])
    """
    for i, arg in enumerate(args):
        if arg in COMMANDS:
            # Comando encontrado en posición i
            # Args del comando = todo lo que está antes de i + todo lo que está después
            cmd_args = list(args[:i])
            if i + 1 < len(args):
                cmd_args.extend(args[i+1:])
            return arg, cmd_args
    return None


def _dispatch(args: list[str]) -> int:
    """Procesa args como [target, arg1, arg2, ...].

    Retorna exit_code.
    """
    if not args:
        cmd_help([])
        return 0

    raw = args[0]
    rest = args[1:]

    # Intentar como agents/<id>/... (path completo en un solo arg)
    code = _try_agents_path(raw, rest)
    if code is not None:
        return code

    # Intentar como "agents" seguido de <id>...
    if raw == "agents":
        if not rest:
            from bootstrap.cli.project import _list_projects, _show_projects_dashboard
            projects = _list_projects()
            return _show_projects_dashboard(projects)
        return _handle_agents_path(rest[0], rest[1:])

    # Buscar comando en toda la lista de args
    found = _find_command_in_args(args)
    if found:
        target, cmd_args = found
        # Si el comando está en posición 0, los args reales están en rest
        # (e.g. "switch delta-skill" → cmd_args=[], rest=["delta-skill"])
        if not cmd_args and rest:
            cmd_args = rest
        func, min_args, max_args = COMMANDS[target]
        if len(cmd_args) < min_args:
            print(f"USO: walker3 {target} ... (faltan argumentos, mínimo {min_args})")
            return 1
        return func(cmd_args)

    # Intentar como ruta con prefijo: "comando/arg..." (path compacto)
    resolved = _resolve_project_command(raw)
    if resolved:
        target, cmd_args = resolved
        full_args = cmd_args + rest
        func, min_args, max_args = COMMANDS[target]
        if len(full_args) < min_args:
            print(f"USO: walker3 {target} ... (faltan argumentos, mínimo {min_args})")
            return 1
        return func(full_args)

    # No se encontró comando
    print(f"Comando no reconocido: {raw}")
    print("Usa 'walker3 help' para ver comandos disponibles.")
    return 1


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada principal del CLI.

    walker_v3.py llama a esta función con sys.argv[1:].
    """
    if argv is None:
        argv = sys.argv[1:]

    try:
        if not argv:
            from bootstrap.cli.project import _list_projects, _show_projects_dashboard
            projects = _list_projects()
            return _show_projects_dashboard(projects)

        return _dispatch(argv)
    except FileNotFoundError as e:
        print(f"\n  ❌ {e}")
        return 1
