"""task_execution/infrastructure/kg_context.py — Resolución de contexto KG.

Glue de infraestructura del task layer (ADR-051 G3): resuelve el searcher,
el project_root y el trace de queries KG vía DI. NO contiene lógica de
negocio — es cómo el task adapter obtiene sus dependencias del composition
root (service locator).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from shared.common.shared_domain import ExecutionContext

logger = logging.getLogger(__name__)


# ADR-WALKER-062 (consistencia F3): el tracer se resuelve por id+contrato
# desde el composition root (AppContainer). Se cachea a nivel modulo para no
# reconstruir el grafo en cada kg_query. Best-effort: si falla, se omite.
_TRACER_CACHE = None  # None=no resuelto; False=resolucion fallida


def _get_tracer():
    global _TRACER_CACHE
    if _TRACER_CACHE is None:
        try:
            # ADR-062: id+contrato via resolve_locator — no importar app/.
            from shared.core.engine.bootstrap import resolve_locator

            _TRACER_CACHE = resolve_locator("tracer")
        except Exception:
            _TRACER_CACHE = False
    return _TRACER_CACHE if _TRACER_CACHE is not False else None


def _trace_kg_query(
    chain_dir: str, index: str, query: dict, ctx: ExecutionContext
) -> None:
    """Registra un evento kg_query en el TraceGraph (best-effort con log)."""
    try:
        # ADR-WALKER-062 (consistencia F3): resolver el tracer por id+contrato
        # desde el composition root (AppContainer), no construir el concreto.
        tracer = _get_tracer()
        safe_query = {}
        for k, v in (query or {}).items():
            if isinstance(v, str) and len(v) > 200:
                safe_query[k] = v[:200] + "..."
            else:
                safe_query[k] = v
        if tracer is not None:
            tracer.trace(
                chain_dir,
                "kg_query",
                data={
                    "index": index,
                    "query": safe_query,
                    "project_id": getattr(ctx, "project_id", ""),
                },
                task_id=ctx.task_id or "",
                session_id=ctx.session_id or "",
            )
    except Exception as e:
        logger.warning(
            "kg: _trace_kg_query no pudo persistir el evento (%s: %s)",
            type(e).__name__, e,
        )


def _build_searcher_local(project_root: str) -> Any:
    """Fallback CLI-mode (sin service locator): construye el searcher.

    Réplica del composition root (registry._build_searcher) sin acoplar el
    task layer a features.main: CognitiveGraph(memory_data_dir) +
    register_cognitive_indexes → CognitiveGraphSearcher.
    """
    from app.cognitive_registry import (
        register_cognitive_indexes,
        memory_data_dir,
    )
    from shared.libs.cognitive_graph import CognitiveGraph
    from shared.libs.cognitive_graph.searcher import CognitiveGraphSearcher
    from app.memory_schemas import register_default_memory_indexes

    graph = CognitiveGraph(memory_data_dir(project_root), register_defaults=register_default_memory_indexes)
    register_cognitive_indexes(graph)
    return CognitiveGraphSearcher(graph)


def _resolve_searcher_factory() -> Any:
    """Factory del CognitiveGraphSearcher vía DI (ADR-045/058).

    Prioridad: service locator neutro (server: clave 'cognitive_searcher'
    registrada por el composition root). Fallback CLI-mode: _build_searcher_local
    (el CLI no fija set_service_locator para las tasks KG).
    """
    from shared.core.engine.bootstrap import resolve_locator

    service = resolve_locator("cognitive_searcher")
    if callable(service):
        return service
    return _build_searcher_local


def _resolve_searcher(chain_dir: str, project_id: str = "", searcher_factory=None) -> Any:
    """Resuelve CognitiveGraphSearcher desde chain_dir o project_id (sin sprint)."""
    from features.project import ProjectService

    if searcher_factory is None:
        searcher_factory = _resolve_searcher_factory()
    if searcher_factory is None:
        raise ValueError(
            "_resolve_searcher requiere searcher_factory inyectado. "
            "Pásalo desde el composition root."
        )

    if chain_dir:
        try:
            paths = ProjectService.resolve_paths(chain_dir)
            return searcher_factory(paths.project_root)
        except Exception as e:
            logger.warning(
                "kg: _get_searcher(%s) fallo (%s: %s); intento por registry",
                chain_dir, type(e).__name__, e,
            )
    if project_id:
        from app import path_resolver

        registry_path = path_resolver.get_walker_root() / "data" / "projects.json"
        if registry_path.exists():
            with open(registry_path) as f:
                reg = json.load(f).get("projects", {}).get(project_id, {})
            repo_path = reg.get("repo_path", "")
            if repo_path:
                cog_proj = os.path.join(repo_path, ".cognitive", "projects", project_id)
                if os.path.isdir(cog_proj):
                    return searcher_factory(str(cog_proj))
    return None


def _resolve_ckg_project_root(project_id: str, path_resolver=None) -> str:
    """Resuelve project_root desde registry para operaciones CKG sin sprint."""
    if path_resolver is None:
        raise ValueError(
            "_resolve_ckg_project_root requiere path_resolver inyectado. "
            "Pásalo desde el composition root."
        )
    registry_path = path_resolver.get_walker_root() / "data" / "projects.json"
    if registry_path.exists():
        with open(registry_path) as f:
            reg = json.load(f).get("projects", {}).get(project_id, {})
        repo_path = reg.get("repo_path", "")
        if repo_path:
            cog_proj = os.path.join(repo_path, ".cognitive", "projects", project_id)
            if os.path.isdir(cog_proj):
                return str(cog_proj)
    return ""


def _resolve_project_root(chain_dir: str, project_id: str = "") -> str:
    """Resuelve project_root desde chain_dir o project_id."""
    if chain_dir:
        from features.project import ProjectService

        try:
            paths = ProjectService.resolve_paths(chain_dir)
            return str(paths.project_root)
        except Exception:
            pass
    if project_id:
        return _resolve_ckg_project_root(project_id)
    return ""


def resolve_cpg_ckg_services(
    chain_dir: str, project_id: str = ""
) -> tuple[Any | None, Any | None, str]:
    """Resuelve CpgService + CkgApplicationService + project_root (glue DI).

    Punto único de composición del task layer (ADR-051 G4): los consumers
    no deciden cómo se arma el CpgQueryService ni el CKG — delegan aquí.
    Returns (cpg, ckg, project_root) con cpg/ckg None si no hay contexto
    resolvible; project_root solo como diagnóstico.
    """
    from returns.pipeline import is_successful
    from returns.result import safe

    @safe
    def _resolve_project_root_core(pid: str) -> str:
        from features.project import ProjectService

        project = ProjectService.from_project_id(pid)
        return str(project.paths.project_root)

    @safe
    def _resolve_cpg_core(ctx_dir: str):
        from features.code_analysis.application.services.cpg_service import (
            CpgService,
        )

        return CpgService.from_chain_dir(ctx_dir)

    def _safe_build(result, label: str):
        if is_successful(result):
            return result.value_or(None)
        error = result.failure()
        logger.warning(
            "kg_context: %s no disponible (%s: %s)",
            label, type(error).__name__, error,
        )
        return None

    project_root = ""
    if chain_dir:
        from features.project import ProjectService as _PS

        paths = _PS.resolve_paths(chain_dir)
        project_root = str(paths.project_root)
    elif project_id:
        project_root = _safe_build(
            _resolve_project_root_core(project_id), "project_root"
        ) or ""

    if not project_root or not os.path.isdir(project_root):
        return None, None, project_root

    cpg_context = chain_dir if chain_dir else project_root
    cpg = _safe_build(_resolve_cpg_core(cpg_context), "CPG")
    from features.cognitive_knowledge.application.services.ckg_service import (
        CkgApplicationService,
    )

    return cpg, CkgApplicationService, project_root
