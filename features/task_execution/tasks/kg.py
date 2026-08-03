"""KG query tasks — thin adapters to libs/infrastructure searcher."""
from __future__ import annotations
import json
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from features.project import ProjectService
from libs.infrastructure import CognitiveGraphSearcher


def _get_searcher(chain_dir: str) -> CognitiveGraphSearcher:
    paths = ProjectService.resolve_paths(chain_dir)
    return CognitiveGraphSearcher(paths.project_root)


def _trace_kg_query(chain_dir: str, index: str, query: dict, ctx: ExecutionContext) -> None:
    """Registra un evento kg_query en el TraceGraph (best-effort)."""
    try:
        from features.engine.infrastructure.trace_log import TraceLogger
        tracer = TraceLogger()
        # Limitar payload para no inflar el trace
        safe_query = {}
        for k, v in (query or {}).items():
            if isinstance(v, str) and len(v) > 200:
                safe_query[k] = v[:200] + "..."
            else:
                safe_query[k] = v
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
    except Exception:
        pass


_KG_HELP = {
    "protocol": (
        "PROTOCOLO: Antes de leer archivos del codigo fuente, consulta las memorias.\n"
        "Cada memoria responde una pregunta distinta. Usa kg_schema para descubrir\n"
        "los campos de cada indice, luego construye queries precisas."
    ),
    "indices": {
        "delta": "Tareas del sprint (campos: task_id, objective, domain, status, files). Busqueda full-text por texto.",
        "ckg": "Conocimiento ingenieril (campos: type, statement, scope, tags). Busqueda estructurada o full-text.",
        "cpg": "Estructura del codigo (campos: name, type, file, line). Busqueda estructurada por file/function/type.",
        "trace": "Historial de ejecucion (campos: event_type, tool, task_id, session_id). Busqueda estructurada o full-text.",
    },
    "ejemplos": [
        {"task": "kg_schema", "desc": "Descubrir todos los indices disponibles",
         "cmd": "walker <project>/kg_schema"},
        {"task": "kg_schema", "desc": "Ver schema de un indice (campos y tipos)",
         "cmd": "walker <project>/kg_schema '{\"index\":\"delta\"}'"},
        {"task": "kg_search", "desc": "Busqueda full-text en ckg (conocimiento)",
         "cmd": "walker <project>/kg_search '{\"text\":\"authentication\"}'"},
        {"task": "kg_search", "desc": "Busqueda full-text en un indice especifico (delta)",
         "cmd": "walker <project>/kg_search '{\"_index\":\"delta\",\"text\":\"authentication\"}'"},
        {"task": "kg_search", "desc": "Busqueda estructurada en cpg (codigo por archivo)",
         "cmd": "walker <project>/kg_search '{\"_index\":\"cpg\",\"file\":\"src/auth.py\"}'"},
        {"task": "kg_search", "desc": "Busqueda por tipo en ckg (conocimiento)",
         "cmd": "walker <project>/kg_search '{\"type\":\"decision\"}'"},
        {"task": "kg_multi", "desc": "Query federada en 3 indices a la vez",
         "cmd": "walker <project>/kg_multi '{\"delta\":{\"text\":\"auth\"},\"cpg\":{\"file\":\"src/auth.py\"},\"ckg\":{\"scope\":\"security\"}}'"},
    ],
}


def _kg_help(topic: str = "") -> dict:
    """Retorna ayuda sobre las memorias de CognitiveGraph."""
    return {
        "type": "kg_help",
        "topic": topic,
        "protocol": _KG_HELP["protocol"],
        "indices": _KG_HELP["indices"],
        "ejemplos": _KG_HELP["ejemplos"],
    }


def _resolve_searcher(chain_dir: str, project_id: str = ""):
    """Resuelve CognitiveGraphSearcher desde chain_dir o project_id (sin sprint)."""
    if chain_dir:
        try:
            return _get_searcher(chain_dir)
        except Exception:
            pass
    if project_id:
        from libs.path_resolver import get_walker_root
        import json, os
        registry_path = get_walker_root() / "data" / "projects.json"
        if registry_path.exists():
            with open(registry_path) as f:
                reg = json.load(f).get("projects", {}).get(project_id, {})
            repo_path = reg.get("repo_path", "")
            if repo_path:
                cog_proj = os.path.join(repo_path, ".cognitive", "projects", project_id)
                if os.path.isdir(cog_proj):
                    return CognitiveGraphSearcher(str(cog_proj))
    return None


def kg_schema(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    project_id = data.get("_project_id", "")
    cg = _resolve_searcher(ctx.chain_dir or "", project_id)
    if cg is None:
        return TaskResult.failure("No project context", data={
            "error": "No active sprint and no project context",
            "next_step": "Run 'walker <project>/status' or 'walker <project>/build_initial_ckg' first",
        })
    # REST: body JSON via _payload → {"index": "delta"}
    payload = data.get("_payload", None)
    if payload and isinstance(payload, dict):
        index = payload.get("index", "")
        if index:
            try:
                return TaskResult.success(data=cg.schema(index))
            except KeyError:
                return TaskResult.failure(f"Index '{index}' not found. Disponibles: {cg.list_indexes()}")
    # Legacy: flag --index
    index = data.get("index", "")
    if index:
        try:
            return TaskResult.success(data=cg.schema(index))
        except KeyError:
            return TaskResult.failure(f"Index '{index}' not found")
    indices = cg.list_indexes()
    return TaskResult.success(data={
        "type": "kg_schema",
        "indexes": indices,
        "help": _kg_help("kg_schema"),
    })


def kg_search(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    project_id = data.get("_project_id", "")
    cg = _resolve_searcher(ctx.chain_dir or "", project_id)
    if cg is None:
        return TaskResult.failure("No project context", data={
            "error": "No active sprint and no project context",
            "next_step": "Run 'walker <project>/status' or 'walker <project>/kg_extract' first",
        })
    
    # REST: body directo via _payload
    payload = data.get("_payload", None)
    if payload and isinstance(payload, dict):
        # Caso: solo _index sin filtros -> listar el indice completo
        if set(payload.keys()) == {"_index"} or (len(payload) == 1 and "_index" in payload):
            index = payload.get("_index", "ckg")
            _trace_kg_query(ctx.chain_dir or "", index, {}, ctx)
            try:
                return TaskResult.success(data=cg.search(index, {}))
            except KeyError:
                return TaskResult.failure(f"Index '{index}' not found. Disponibles: {cg.list_indexes()}")
        has_single_query_keys = any(
            k in payload for k in (
                "text", "type", "file", "function", "scope", "status",
                "severity", "tag", "label", "task", "description",
                "signature", "kind", "domain", "include_superseded",
            )
        )
        if not has_single_query_keys and len(payload) > 0:
            _trace_kg_query(ctx.chain_dir or "", "multi", payload, ctx)
            return TaskResult.success(data=cg.search_many(payload))
        index = payload.get("_index", "ckg")
        query = {k: v for k, v in payload.items() if k != "_index"}

        # Ciclo de vida CKG: incluir decisiones superseded/deprecated
        include_superseded = query.pop("include_superseded", False)
        if index == "ckg" and include_superseded:
            try:
                from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph
                project_root = _resolve_ckg_project_root(project_id)
                if project_root:
                    ckg = CodeKnowledgeGraph(project_root)
                    nodes = ckg.query({**query, "include_superseded": True})
                    return TaskResult.success(data={
                        "type": "kg_search",
                        "index": "ckg",
                        "hits": len(nodes),
                        "results": nodes,
                    })
            except Exception:
                pass  # fallback al searcher normal

        _trace_kg_query(ctx.chain_dir or "", index, query, ctx)
        try:
            return TaskResult.success(data=cg.search(index, query))
        except KeyError:
            return TaskResult.failure(f"Index '{index}' not found. Disponibles: {cg.list_indexes()}")
    
    # Legacy: flags --index y --query
    index = data.get("index", "")
    query_raw = data.get("query", "")
    if not index or not query_raw:
        return TaskResult.success(data=_kg_help("kg_search"))
    try:
        query = json.loads(query_raw) if isinstance(query_raw, str) else query_raw
    except json.JSONDecodeError:
        return TaskResult.failure(f"Invalid query JSON: {query_raw}")
    _trace_kg_query(ctx.chain_dir or "", index, query, ctx)
    try:
        return TaskResult.success(data=cg.search(index, query))
    except KeyError:
        return TaskResult.failure(f"Index '{index}' not found. Disponibles: {cg.list_indexes()}")


def kg_multi(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    project_id = data.get("_project_id", "")
    cg = _resolve_searcher(ctx.chain_dir or "", project_id)
    if cg is None:
        return TaskResult.failure("No project context", data={
            "error": "No active sprint and no project context",
            "next_step": "Run 'walker <project>/status' or 'walker <project>/kg_extract' first",
        })
    
    # REST: body directo via _payload
    payload = data.get("_payload", None)
    if payload and isinstance(payload, dict):
        _trace_kg_query(ctx.chain_dir or "", "multi", payload, ctx)
        return TaskResult.success(data=cg.search_many(payload))
    
    # Legacy: flag --queries
    queries_raw = data.get("queries", "")
    if not queries_raw:
        return TaskResult.success(data=_kg_help("kg_multi"))
    try:
        queries = json.loads(queries_raw) if isinstance(queries_raw, str) else queries_raw
    except json.JSONDecodeError:
        return TaskResult.failure(f"Invalid queries JSON: {queries_raw}")
    _trace_kg_query(ctx.chain_dir or "", "multi", queries, ctx)
    return TaskResult.success(data=cg.search_many(queries))


def _resolve_ckg_project_root(project_id: str) -> str:
    """Resuelve project_root desde registry para operaciones CKG sin sprint."""
    from libs.path_resolver import get_walker_root
    import json, os
    registry_path = get_walker_root() / "data" / "projects.json"
    if registry_path.exists():
        with open(registry_path) as f:
            reg = json.load(f).get("projects", {}).get(project_id, {})
        repo_path = reg.get("repo_path", "")
        if repo_path:
            cog_proj = os.path.join(repo_path, ".cognitive", "projects", project_id)
            if os.path.isdir(cog_proj):
                return str(cog_proj)
    return ""


def kg_store(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    project_id = data.get("_project_id", "")

    # Resolver project_root desde chain_dir o desde registry
    project_root = ""
    if chain_dir:
        from features.project import ProjectService
        try:
            paths = ProjectService.resolve_paths(chain_dir)
            project_root = str(paths.project_root)
        except Exception:
            pass
    elif project_id:
        project_root = _resolve_ckg_project_root(project_id)

    if not project_root:
        return TaskResult.failure("No project context", data={
            "error": "No active sprint and no project context available",
            "next_step": "Run 'walker <project>/status' or 'walker <project>/build_initial_ckg' first",
        })

    from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph
    try:
        # Leer datos desde _payload (REST) o desde flags directos (legacy)
        payload = data.get("_payload", data)
        statement = payload.get("statement", payload.get("label", ""))
        rationale = payload.get("rationale", "")
        node_type = payload.get("type", "decision")
        supersedes = payload.get("supersedes", []) or []
        metadata = payload.get("metadata", {}) or {}

        if not statement or not statement.strip():
            return TaskResult.failure("Empty statement", data={
                "error": "Cannot save empty knowledge. Provide --statement or 'statement' field",
                "next_step": "kg_store --type decision --statement 'Clear description of the knowledge'",
            })

        ckg = CodeKnowledgeGraph(project_root)
        # Normalizar scope/severity a metadata (KnowledgeFact no tiene campos
        # top-level para ellos; la convencion legacy los guarda en metadata).
        node_meta = dict(metadata)
        if "scope" in payload and "scope" not in node_meta:
            node_meta["scope"] = payload.get("scope")
        if "severity" in payload and "severity" not in node_meta:
            node_meta["severity"] = payload.get("severity")
        node = {
            "label": statement,
            "description": rationale,
            "type": node_type,
            "tags": payload.get("tags", []) or [],
            "metadata": node_meta,
        }
        if supersedes:
            # Ciclo de vida: esta decision reemplaza/degrada a otras anteriores
            node["supersedes"] = supersedes
        result = ckg.save_nodes([node])
        return TaskResult.success(data={
            "ok": result.get("ok", False),
            "nodes_saved": result.get("nodes_created", 0),
            "degraded": result.get("degraded", []),
            "note": (
                "La nueva decision reemplaza a las previas; estas quedaron "
                "con status=superseded y solo se ven con include_superseded."
                if supersedes else "Decision activa registrada."
            ),
            "next_step": "Run 'walker <project>/kg_search' to verify saved knowledge",
        })
    except Exception as e:
        return TaskResult.failure(str(e), data={
            "error": f"CKG write failed: {e}",
            "next_step": "Run 'walker <project>/build_initial_ckg' to initialize CKG first",
        })


def register(registry: Any) -> None:
    for name, fn, meta in [
        ("kg_schema", kg_schema, {
            "description": "Descubre indices disponibles o schema de un indice especifico",
            "usage": "walker <project>/kg_schema",
            "example": "walker <project>/kg_schema '{\"index\":\"delta\"}'",
            "query_instructions": (
                "Sin body: lista todos los indices disponibles. "
                "Con body {\"index\":\"delta\"}: muestra los campos y tipos de ese indice."
            ),
            "tags": ["kg", "cognitive-graph"],
        }),
        ("kg_search", kg_search, {
            "description": "Busca en un indice de CognitiveGraph (delta, ckg, cpg, trace)",
            "usage": "walker <project>/kg_search '{\"campo\":\"valor\"}'",
            "example": "walker <project>/kg_search '{\"_index\":\"delta\",\"text\":\"authentication\"}'",
            "query_instructions": (
                "Body es la query JSON. Usa {\"text\":\"...\"} para full-text. "
                "Usa {\"campo\":\"valor\"} para busqueda estructurada. "
                "Con _index eliges el indice (default ckg). Primero usa kg_schema para ver campos."
            ),
            "tags": ["kg", "cognitive-graph"],
        }),
        ("kg_store", kg_store, {
            "description": "Almacena un documento en CKG (conocimiento ingenieril). Soporta ciclo de vida: con 'supersedes' (lista de statements o IDs) degrada a las decisiones anteriores reemplazadas.",
            "usage": "walker <project>/kg_store '{\"type\":\"decision\",\"statement\":\"...\",\"supersedes\":[\"<statement o id previo>\"]}'",
            "tags": ["kg", "cognitive-graph"],
        }),
        ("kg_multi", kg_multi, {
            "description": "Query federada en multiples indices a la vez",
            "usage": "walker <project>/kg_multi '{\"delta\":{},\"ckg\":{}}'",
            "example": "walker <project>/kg_multi '{\"delta\":{\"text\":\"auth\"},\"cpg\":{\"file\":\"src/auth.py\"}}'",
            "query_instructions": (
                "Cada clave del body es un indice (delta, ckg, cpg, trace) y cada valor "
                "es la query para ese indice. Recibe resultados de todos en una sola respuesta."
            ),
            "tags": ["kg", "cognitive-graph"],
        }),
    ]:
        registry.register(name, fn, metadata=meta)
