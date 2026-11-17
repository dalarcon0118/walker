"""fast_mcp/memories/handlers/knowledge.py — Knowledge tool handlers.

Each handler receives a resolved MemoryContext (no repeated resolve_paths)
and returns a dict. Consistent error handling via the dispatch() wrapper.
"""
from __future__ import annotations

from typing import Any

from fast_mcp.memories.context import MemoryContext


def handle_knowledge_query(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Query project knowledge: search, save, activate, supersede or extract context."""
    from returns.pipeline import is_successful

    from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import CkgReadService
    from fast_mcp.features.cognitive_knowledge.application.services.ckg_write import CkgWriteService

    action = kwargs.get("action", "search")

    if action == "search":
        query = kwargs.get("query", kwargs.get("text", ""))
        result = CkgReadService.gather_context(ctx.chain_dir, query=query)
        return {"action": action, "result": result.value_or([]) if is_successful(result) else []}

    if action == "save":
        nodes = kwargs.get("nodes", [])
        task_id = kwargs.get("task_id", "")
        result = CkgWriteService.save_nodes(ctx.chain_dir, nodes, task_id=task_id)
        return {
            "action": action,
            "result": result.value_or({}) if is_successful(result) else {"error": str(result.failure())},
        }

    if action == "active":
        type_name = kwargs.get("type", "")
        return {"action": action, "result": CkgReadService.query_active(ctx.chain_dir, type_name=type_name)}

    if action == "context":
        files = kwargs.get("files", [])
        return {"action": action, "result": CkgReadService.query_by_files(ctx.chain_dir, files)}

    # Fallback: treat unknown action as search
    query = kwargs.get("query", action)
    result = CkgReadService.gather_context(ctx.chain_dir, query=query)
    return {"action": action, "result": result.value_or([]) if is_successful(result) else []}


def handle_extract_knowledge(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Extract knowledge from code via CPG to populate CKG."""
    from fast_mcp.features.cognitive_knowledge.application.services.ckg_extract import CkgExtractService
    from fast_mcp.features.cognitive_knowledge import ckg_analysis_adapter

    cpg = ctx.cpg()
    ckg = ckg_analysis_adapter(ctx.chain_dir)
    if cpg is None or ckg is None:
        return {"error": "CPG or CKG not available. Run 'walker prepare <path>' first."}

    scope = kwargs.get("scope", "all")
    payload = CkgExtractService.build_extract_payload(cpg, scope, ctx.chain_dir)
    return {"task": "extract_knowledge", "description": "Code analysis via CPG", **payload}


def handle_memory_schema(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Discover available indexes or schema of a specific index."""
    ckg = ctx.ckg()
    if ckg is None:
        return {"error": "CKG not available"}

    index = kwargs.get("index", "")
    if index:
        try:
            return ckg.schema(index)
        except KeyError:
            return {"type": "error", "message": f"Index '{index}' not found"}

    return {"type": "schema", "indexes": ckg.list_indexes()}


def handle_recall_knowledge(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Search in a CognitiveGraph index (delta, ckg, cpg, trace)."""
    ckg = ctx.ckg()
    if ckg is None:
        return {"error": "CKG not available"}

    payload = dict(kwargs)
    payload.pop("action", None)
    index = payload.pop("_index", payload.pop("index", "ckg"))

    # Los args de PLOMERÍA (project_id, paginación, controles `_`) no son
    # filtros de datos: si llegan a `query`, el router los trata como filtro
    # estructurado y cae al path eager → 0 hits con cualquier texto. Medido
    # 2026-11-16: `recall_knowledge {query: "AppProviders"}` devolvía 0 mientras
    # `search_memories {ckg: {text: ...}}` devolvía 2, por el `project_id`
    # colado en los filtros.
    _CONTROL = {
        "_project_id", "project_id", "project", "max_results", "size",
        "max_events", "_from", "from", "query", "text", "text_fields",
    }
    query = {
        k: v
        for k, v in payload.items()
        if k not in _CONTROL and not k.startswith("_")
    }
    if "query" in payload and "text" not in payload:
        query["text"] = payload["query"]
    elif "text" in payload:
        query["text"] = payload["text"]
    for meta in ("max_results", "size", "max_events", "_from", "from"):
        if meta in payload:
            query[meta] = payload[meta]

    try:
        return ckg.schema(index) if not query else ckg.search_many({index: query})
    except KeyError:
        return {"type": "error", "message": f"Index '{index}' not found"}


def handle_documenting_knowledge(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Store knowledge in CKG (with supersedes lifecycle)."""
    from returns.pipeline import is_successful

    from fast_mcp.features.cognitive_knowledge.application.services.ckg_common import build_ckg_node
    from fast_mcp.features.cognitive_knowledge.application.services.ckg_write import CkgWriteService

    payload = kwargs.get("_payload", kwargs)
    nodes = payload.get("nodes")

    if isinstance(nodes, list) and nodes:
        result = CkgWriteService.save_nodes(ctx.chain_dir, nodes)
        return result.value_or({}) if is_successful(result) else {"error": str(result.failure())}

    node = build_ckg_node(payload)
    result = CkgWriteService.save_nodes(ctx.chain_dir, [node])
    return result.value_or({}) if is_successful(result) else {"error": str(result.failure())}


def _env_skills(project_root: str):
    from fast_mcp.shared_kernel.paths import walker_root
    from fast_mcp.shared_kernel.skills import discover, skills_roots

    return discover(skills_roots(project_root or None, walker_root()))


def handle_search_skills(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Busca skills del ENTORNO del agente por intención (texto/tags).

    El workflow declara la intención de la fase, no ids: qué skills existen
    depende de lo que el agente tenga instalado.
    """
    from fast_mcp.shared_kernel.skills import search

    query = str(kwargs.get("query") or kwargs.get("needs") or "")
    tags = kwargs.get("tags") or ()
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    limit = int(kwargs.get("limit") or 6)
    try:
        hits = search(_env_skills(ctx.project_root), query, tuple(str(t) for t in tags),
                      limit=limit)
    except Exception as exc:  # noqa: BLE001 — el índice no debe romper la tool
        return {"count": 0, "skills": [], "error": f"{type(exc).__name__}: {exc}"}
    return {
        "count": len(hits),
        "skills": [
            {"name": s.name, "description": s.description, "scope": s.scope,
             "path": str(s.path)}
            for s in hits
        ],
    }


def handle_load_skill(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Devuelve el contenido de un skill (progressive disclosure)."""
    name = str(kwargs.get("name") or kwargs.get("skill") or "").strip()
    if not name:
        return {"error": "name es obligatorio"}
    skills = _env_skills(ctx.project_root)
    skill = skills.get(name)
    if skill is None:
        return {
            "error": f"skill '{name}' no encontrado en este entorno",
            "available": sorted(skills)[:20],
        }
    body = skill.body()
    return {
        "name": skill.name,
        "description": skill.description,
        "scope": skill.scope,
        "path": str(skill.path),
        "chars": len(body),
        "content": body,
    }


def handle_search_memories(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Federated query across multiple indexes at once."""
    ckg = ctx.ckg()
    if ckg is None:
        return {"error": "CKG not available"}

    queries = kwargs.get("_payload", kwargs)
    if isinstance(queries, dict):
        try:
            return ckg.search_many(queries)
        except (KeyError, Exception) as e:
            return {"error": str(e)}
    return {"error": "Invalid queries format"}


def handle_agent_context(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Consolidated delta+ckg+cpg+trace query with cross-references."""
    from returns.pipeline import is_successful

    from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import CkgReadService

    body = kwargs.get("_payload", kwargs)
    ckg_q = body.get("ckg", {}) or {}
    query = ckg_q.get("text", ckg_q.get("query", ""))
    result = CkgReadService.gather_context(ctx.chain_dir, query=query)
    return {
        "type": "agent_context",
        "project_id": kwargs.get("_project_id", ctx.project_id),
        "ckg": {"results": result.value_or([]) if is_successful(result) else []},
    }


def handle_index_repo(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Index an external repo as reference in CKG."""
    from returns.pipeline import is_successful

    from fast_mcp.features.cognitive_knowledge.application.services.ckg_write import CkgWriteService

    url = kwargs.get("url", "")
    if not url:
        return {"error": "url is required"}

    repo_name = kwargs.get("name", url.rstrip("/").split("/")[-1])
    branch = kwargs.get("branch", "main")

    # Inyecta el outline provider (regla §3bis R1): este handler es un
    # ADAPTADOR de surface (fast_mcp/memories) y puede cruzar features;
    # el use case del dominio NO importa code_analysis.
    from fast_mcp.features.code_analysis.infrastructure.ast_grep_adapter import (
        parse_outline,
        run_outline,
    )

    result = CkgWriteService.index_repo(
        ctx.chain_dir, url, repo_name, branch, outline=(run_outline, parse_outline)
    )
    return result.value_or({}) if is_successful(result) else {"error": str(result.failure())}
