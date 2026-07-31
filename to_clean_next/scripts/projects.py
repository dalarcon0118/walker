#! /usr/bin/env python3
"""projects.py — Multi-project registry (PROXY).

PROXY MODULE: Re-exports from _walker/workflow_engine/domain/project_registry.py.
New code should import directly from the _walker module.
"""

from __future__ import annotations

import json

from _walker.workflow_engine.domain.project_registry import (
    ProjectRegistry,
    ProjectProfile,
)


# ── CLI ──────────────────────────────────────────────────────


def _cli_list(registry: ProjectRegistry, verbose: bool = False) -> None:
    projects = registry.list_projects()
    if not projects:
        print("No hay proyectos registrados.")
        return
    print(f"Proyectos registrados ({len(projects)}):")
    print()
    hdr = f"  {'ID':<25} {'Nombre':<30} {'Estado':<10} {'Ruta'}"
    print(hdr)
    for pid, info in sorted(projects.items()):
        name = info.get("name", pid)[:28]
        active = "activo" if info.get("active") else "pausado"
        repo = info.get("repo_path", "?")
        print(f"  {pid:<25} {name:<30} {active:<10} {repo}")
    print()


def _cli_register(registry: ProjectRegistry, args: list[str]) -> None:
    if len(args) < 2:
        print("Uso: projects.py register <project_id> <repo_path> [name]")
        return
    project_id = args[0]
    repo_path = args[1]
    name = args[2] if len(args) > 2 else ""
    registry.register_project(project_id, repo_path, name=name)
    print(f"Proyecto '{project_id}' registrado en {repo_path}")


def _cli_show(registry: ProjectRegistry, args: list[str]) -> None:
    if not args:
        print("Uso: projects.py show <project_id>")
        return
    pid = args[0]
    profile = ProjectProfile(cognitive_root=registry._root)
    data = profile.load(pid)
    if data is None:
        print(f"Proyecto '{pid}' no encontrado o sin project.json")
        return
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _cli_init_profile(registry: ProjectRegistry, args: list[str]) -> None:
    if not args:
        print("Uso: projects.py init-profile <project_id> [name]")
        return
    pid = args[0]
    name = args[1] if len(args) > 1 else pid
    info = registry.get_project(pid)
    if not info:
        print(f"Proyecto '{pid}' no esta registrado. Registralo primero.")
        return
    profile = ProjectProfile(cognitive_root=registry._root)
    existing = profile.load(pid)
    if existing:
        print(f"El project.json para '{pid}' ya existe. Usa 'show' para verlo.")
        return
    profile.create_default(
        project_id=pid,
        name=name,
        repo_path=info.get("repo_path", ""),
    )
    profile.save()
    print(f"project.json creado para '{pid}'")
