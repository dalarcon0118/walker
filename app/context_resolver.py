"""shared/core/context_resolver.py — ContextResolver (R1, ADR-WALKER-055).

Primitiva compartida que resuelve la raíz de memorias canónica de un proyecto
desde un chain_dir: project_id + repo_root + data_dir.

Motivación (patrón R1): la resolución de paths/repo se repetía ~65 veces en
los features (resolve_paths, _resolve_repo, _resolve_repo_and_data_dir,
_resolve_default_paths, memory_data_dir...). Esta es la ÚNICA aritmética;
los features delegan.

Usa app.path_resolver (capa neutral — sin imports de features).
Defensivo: sin manifest, prueba el layout canónico <chain_dir>/data.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryContext:
    """Raíz de memorias canónica de un proyecto."""

    project_id: str
    repo_root: str
    data_dir: str


def resolve_memory_context(chain_dir: str) -> MemoryContext:
    """Resuelve (project_id, repo_root, data_dir) desde un chain_dir.

    - data_dir: .cognitive/projects/<pid>/data (donde viven las memorias).
    - repo_root: raíz real del código (para métricas/scope).
    - project_id: id lógico del proyecto (manifest) o basename del dir.

    Fallback defensivo (proyectos sin manifest/tests): si <chain_dir>/data
    existe, ese es el data_dir; si no, chain_dir.
    """
    if not chain_dir:
        return MemoryContext(project_id="", repo_root="", data_dir="")

    try:
        import json as _json

        from app.path_resolver import (
            resolve_project_root,
            resolve_project_id,
        )

        root = resolve_project_root(chain_dir)
        pid = resolve_project_id(root) or os.path.basename(root)
        # data_dir SIEMPRE = <root>/data (root ya es el dir del proyecto,
        # .cognitive/projects/<pid>). NO usar get_data_dir/get_repo_path:
        # resuelven por el registry GLOBAL (walker) y rompen descentralizados.
        data_dir = os.path.join(root, "data")
        repo = root
        manifest_path = os.path.join(data_dir, "project.json")
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path) as _f:
                    _proj = _json.load(_f).get("project", {})
                rp = (_proj.get("location") or {}).get("repo_path", "")
                if rp and os.path.isdir(rp):
                    repo = rp
            except Exception:
                pass
        return MemoryContext(project_id=pid, repo_root=repo, data_dir=data_dir)
    except Exception:
        candidate = os.path.join(chain_dir, "data")
        return MemoryContext(
            project_id=os.path.basename(os.path.normpath(chain_dir)),
            repo_root=chain_dir,
            data_dir=candidate if os.path.isdir(candidate) else chain_dir,
        )


def resolve_data_dir(chain_dir: str) -> str:
    """Solo el data_dir canónico (atajo común)."""
    return resolve_memory_context(chain_dir).data_dir