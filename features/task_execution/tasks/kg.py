"""KG query tasks — thin adapters to libs/infrastructure searcher.

Result pattern (dry-python/returns): los cores con @safe nunca lanzan; los
wrappers degradan con fallback explícito + log (convention-001).
"""

from __future__ import annotations
import json
import logging
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from features.project import ProjectService

logger = logging.getLogger(__name__)


def _get_searcher(chain_dir: str) -> Any:
    """Construye el searcher vía DI (key "cognitive_searcher") para chain_dir."""
    from features.engine.bootstrap import get_module_service

    paths = ProjectService.resolve_paths(chain_dir)
    searcher_factory = get_module_service("cognitive_searcher")
    return searcher_factory(paths.project_root)


def _build_searcher_for(project_root: str) -> Any:
    """Construye el searcher vía DI para un project_root explícito."""
    from features.engine.bootstrap import get_module_service

    searcher_factory = get_module_service("cognitive_searcher")
    return searcher_factory(project_root)


def _trace_kg_query(
    chain_dir: str, index: str, query: dict, ctx: ExecutionContext
) -> None:
    """Registra un evento kg_query en el TraceGraph (best-effort con log)."""
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
    except Exception as e:
        logger.warning(
            "kg: _trace_kg_query no pudo persistir el evento (%s: %s)",
            type(e).__name__, e,
        )


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
        {
            "task": "kg_schema",
            "desc": "Descubrir todos los indices disponibles",
            "cmd": "walker <project>/kg_schema",
        },
        {
            "task": "kg_schema",
            "desc": "Ver schema de un indice (campos y tipos)",
            "cmd": 'walker <project>/kg_schema \'{"index":"delta"}\'',
        },
        {
            "task": "kg_search",
            "desc": "Busqueda full-text en ckg (conocimiento)",
            "cmd": 'walker <project>/kg_search \'{"text":"authentication"}\'',
        },
        {
            "task": "kg_search",
            "desc": "Busqueda full-text en un indice especifico (delta)",
            "cmd": 'walker <project>/kg_search \'{"_index":"delta","text":"authentication"}\'',
        },
        {
            "task": "kg_search",
            "desc": "Busqueda estructurada en cpg (codigo por archivo)",
            "cmd": 'walker <project>/kg_search \'{"_index":"cpg","file":"src/auth.py"}\'',
        },
        {
            "task": "kg_search",
            "desc": "Busqueda por tipo en ckg (conocimiento)",
            "cmd": 'walker <project>/kg_search \'{"type":"decision"}\'',
        },
        {
            "task": "kg_multi",
            "desc": "Query federada en 3 indices a la vez",
            "cmd": 'walker <project>/kg_multi \'{"delta":{"text":"auth"},"cpg":{"file":"src/auth.py"},"ckg":{"scope":"security"}}\'',
        },
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
    """Resuelve CognitiveGraphSearcher desde chain_dir o project_id (sin sprint).

    Result pattern (convention-001): ante fallo de resolución se loggea y se
    continúa con el siguiente origen (chain_dir → registry).
    """
    if chain_dir:
        try:
            return _get_searcher(chain_dir)
        except Exception as e:
            logger.warning(
                "kg: _get_searcher(%s) fallo (%s: %s); intento por registry",
                chain_dir, type(e).__name__, e,
            )
    if project_id:
        from features.engine.bootstrap import get_module_service
        import json
        import os

        path_resolver = get_module_service("path_resolver")
        registry_path = path_resolver.get_walker_root() / "data" / "projects.json"
        if registry_path.exists():
            with open(registry_path) as f:
                reg = json.load(f).get("projects", {}).get(project_id, {})
            repo_path = reg.get("repo_path", "")
            if repo_path:
                cog_proj = os.path.join(repo_path, ".cognitive", "projects", project_id)
                if os.path.isdir(cog_proj):
                    return _build_searcher_for(str(cog_proj))
    return None


def kg_schema(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    project_id = data.get("_project_id", "")
    cg = _resolve_searcher(ctx.chain_dir or "", project_id)
    if cg is None:
        return TaskResult.failure(
            "No project context",
            data={
                "error": "No active sprint and no project context",
                "next_step": "Run 'walker <project>/status' or 'walker <project>/build_initial_ckg' first",
            },
        )
    # REST: body JSON via _payload → {"index": "delta"}
    payload = data.get("_payload", None)
    if payload and isinstance(payload, dict):
        index = payload.get("index", "")
        if index:
            try:
                return TaskResult.success(data=cg.schema(index))
            except KeyError:
                return TaskResult.failure(
                    f"Index '{index}' not found. Disponibles: {cg.list_indexes()}"
                )
    # Legacy: flag --index
    index = data.get("index", "")
    if index:
        try:
            return TaskResult.success(data=cg.schema(index))
        except KeyError:
            return TaskResult.failure(f"Index '{index}' not found")
    indices = cg.list_indexes()
    return TaskResult.success(
        data={
            "type": "kg_schema",
            "indexes": indices,
            "help": _kg_help("kg_schema"),
        }
    )


def kg_search(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Busca en un índice de CognitiveGraph (delta, ckg, cpg, trace).

    SSOT con el resto de tasks kg: resuelve el searcher vía _resolve_searcher
    y delega en _kg_search_payload (el core de búsqueda compartido). Así no
    depende de ckg_analyze (que es la API CKG-only y vive en su propio módulo).
    """
    project_id = data.get("_project_id", "")
    cg = _resolve_searcher(ctx.chain_dir or "", project_id)
    if cg is None:
        return TaskResult.failure(
            "No project context",
            data={
                "error": "No active sprint and no project context",
                "next_step": "Run 'walker <project>/status' or 'walker <project>/build_initial_ckg' first",
            },
        )
    payload = data.get("_payload", data)
    if isinstance(payload, str):
        # flags_builder de la ruta kg_search hace a.get("query", {}): si el
        # MCP manda {"query": "mcp"} el _payload llega como string → full-text.
        payload = {"text": payload}
    if isinstance(payload, dict):
        payload.pop("action", None)
        payload.pop("_project_id", None)
        # Alias "query" → "text" (schema MCP de kg_search documenta ambos).
        if "query" in payload and "text" not in payload:
            payload["text"] = payload.pop("query")
        # Alias "index" → "_index": el tool MCP kg_search expone `index` en su
        # schema, pero el core de búsqueda (_kg_search_payload) espera `_index`.
        # ANTES el route flags_builder descartaba `index` y la búsqueda caía al
        # índice default (ckg) sin error — fallo silencioso de dispatch.
        if "index" in payload and "_index" not in payload:
            payload["_index"] = payload.pop("index")
    return _kg_search_payload(ctx, cg, payload, project_id)


def _kg_search_payload(
    ctx: ExecutionContext, cg: Any, payload: dict, project_id: str
) -> TaskResult:
    """Maneja el body REST de kg_search (payload dict)."""
    # Caso: solo _index sin filtros -> listar el indice completo
    if set(payload.keys()) == {"_index"} or (len(payload) == 1 and "_index" in payload):
        index = payload.get("_index", "ckg")
        _trace_kg_query(ctx.chain_dir or "", index, {}, ctx)
        try:
            return TaskResult.success(data=cg.search(index, {}))
        except KeyError:
            return TaskResult.failure(
                f"Index '{index}' not found. Disponibles: {cg.list_indexes()}"
            )

    has_single_query_keys = any(
        k in payload
        for k in (
            "text",
            "type",
            "file",
            "function",
            "scope",
            "status",
            "severity",
            "tag",
            "label",
            "task",
            "description",
            "signature",
            "kind",
            "domain",
            "include_superseded",
        )
    )
    if not has_single_query_keys and len(payload) > 0:
        _trace_kg_query(ctx.chain_dir or "", "multi", payload, ctx)
        return TaskResult.success(data=cg.search_many(_with_compact_defaults(payload)))

    index = payload.get("_index", "ckg")
    query = {k: v for k, v in payload.items() if k != "_index"}

    # Ciclo de vida CKG: incluir decisiones superseded/deprecated
    include_superseded = query.pop("include_superseded", False)
    if index == "ckg" and include_superseded:
        superseded = _kg_search_superseded(project_id, query)
        if superseded:
            return TaskResult.success(data=superseded)

    return _kg_search_index(ctx, cg, index, query)


def _kg_search_superseded(project_id: str, query: dict) -> dict | None:
    """Busca en CKG incluyendo decisiones superseded/deprecated."""
    try:
        from features.cognitive_knowledge import ckg_service

        project_root = _resolve_ckg_project_root(project_id)
        if project_root:
            ckg = ckg_service(project_root)
            nodes = ckg.query({**query, "include_superseded": True})
            return {
                "type": "kg_search",
                "index": "ckg",
                "hits": len(nodes),
                "results": nodes,
            }
    except Exception as e:
        # Fallback al searcher normal: el error se loggea (convention-001).
        logger.warning(
            "kg: búsqueda de superseded fallo (%s: %s); fallback al searcher",
            type(e).__name__, e,
        )
    return None


def _kg_search_index(
    ctx: ExecutionContext, cg: Any, index: str, query: dict
) -> TaskResult:
    """Ejecuta cg.search con manejo de KeyError."""
    _trace_kg_query(ctx.chain_dir or "", index, query, ctx)
    try:
        return TaskResult.success(data=cg.search(index, query))
    except KeyError:
        return TaskResult.failure(
            f"Index '{index}' not found. Disponibles: {cg.list_indexes()}"
        )


def kg_multi(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    project_id = data.get("_project_id", "")
    cg = _resolve_searcher(ctx.chain_dir or "", project_id)
    if cg is None:
        return TaskResult.failure(
            "No project context",
            data={
                "error": "No active sprint and no project context",
                "next_step": "Run 'walker <project>/status' or 'walker <project>/kg_extract' first",
            },
        )

    # REST: body directo via _payload
    payload = data.get("_payload", None)
    if payload and isinstance(payload, dict):
        _trace_kg_query(ctx.chain_dir or "", "multi", payload, ctx)
        return TaskResult.success(data=cg.search_many(_with_compact_defaults(payload)))

    # Legacy: flag --queries
    queries_raw = data.get("queries", "")
    if not queries_raw:
        return TaskResult.success(data=_kg_help("kg_multi"))
    try:
        queries = (
            json.loads(queries_raw) if isinstance(queries_raw, str) else queries_raw
        )
    except json.JSONDecodeError:
        return TaskResult.failure(f"Invalid queries JSON: {queries_raw}")
    _trace_kg_query(ctx.chain_dir or "", "multi", queries, ctx)
    return TaskResult.success(data=cg.search_many(_with_compact_defaults(queries)))


def _with_compact_defaults(queries: dict) -> dict:
    """Inyecta metacampos de poda (A+B+C) por defecto en kg_multi.

    El LLM recibe respuestas compactas sin pedirlo: proyección de campos,
    ranking por relevancia y presupuesto top_k. El caller puede sobreescribir
    con _top_k/_max_chars/_compact propios.
    """
    out = dict(queries or {})
    if isinstance(out, dict):
        out.setdefault("_top_k", 10)
        out.setdefault("_max_chars", 400)
        out.setdefault("_compact", True)
    return out


def _resolve_ckg_project_root(project_id: str) -> str:
    """Resuelve project_root desde registry para operaciones CKG sin sprint."""
    from features.engine.bootstrap import get_module_service
    import json
    import os

    path_resolver = get_module_service("path_resolver")
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


def save_to_ckg(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    project_id = data.get("_project_id", "")

    # Leer datos desde _payload (REST) o desde flags directos (legacy)
    payload = data.get("_payload", data)

    # Formato workflow (CKG_SAVE): data trae "nodes" como lista ya estructurada
    # (thin adapter legacy de knowledge.py -> SaveToCkgUseCase). Se pasa el mismo
    # core canonico; el use case anade task_id/session_id a metadata.
    nodes = payload.get("nodes")
    if isinstance(nodes, list) and nodes:
        if not chain_dir:
            return TaskResult.failure("chain_dir not available")
        from features.cognitive_knowledge.application.use_cases import (
            SaveToCkgUseCase,
        )
        from returns.pipeline import is_successful

        result = SaveToCkgUseCase.execute(
            chain_dir,
            nodes,
            task_id=ctx.task_id or "",
            session_id=ctx.session_id or "",
        )
        if not is_successful(result):
            return TaskResult.failure(str(result.failure()))
        res = result.value_or({})
        return TaskResult.success(
            data={
                "ok": bool(res.get("ok", res.get("saved", False))),
                "nodes_saved": res.get("nodes_saved", 0),
                "saved": bool(res.get("ok", res.get("saved", False))),
                **res,
            }
        )

    # Resolver project_root desde chain_dir o desde registry
    project_root = ""
    if chain_dir:
        from features.project import ProjectService

        try:
            paths = ProjectService.resolve_paths(chain_dir)
            project_root = str(paths.project_root)
        except Exception as e:
            logger.warning(
                "save_to_ckg: resolve_paths(%s) fallo (%s: %s)",
                chain_dir, type(e).__name__, e,
            )
    elif project_id:
        project_root = _resolve_ckg_project_root(project_id)

    if not project_root:
        return TaskResult.failure(
            "No project context",
            data={
                "error": "No active sprint and no project context available",
                "next_step": "Run 'walker <project>/status' or 'walker <project>/build_initial_ckg' first",
            },
        )

    from features.cognitive_knowledge import ckg_service

    try:
        statement = payload.get("statement", payload.get("label", ""))
        rationale = payload.get("rationale", "")
        node_type = payload.get("type", "decision")
        supersedes = payload.get("supersedes", []) or []
        metadata = payload.get("metadata", {}) or {}

        if not statement or not statement.strip():
            # No-op (formato workflow CKG_SAVE con fields vacios): la interna
            # legacy devolvia success sin persistir, no un error.
            if not payload:
                return TaskResult.success(
                    data={"ok": False, "saved": False, "message": "no data to save"}
                )
            return TaskResult.failure(
                "Empty statement",
                data={
                    "error": "Cannot save empty knowledge. Provide --statement or 'statement' field",
                    "next_step": "save_to_ckg --type decision --statement 'Clear description of the knowledge'",
                },
            )

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

        # SSOT (camino canonico): pasar por SaveToCkgUseCase del feature
        # (CognitiveCkgRepository -> CognitiveGraph) cuando hay chain_dir,
        # para que TODAS las tasks de escritura CKG compartan el mismo core
        # y el mismo formato de indice. Sin chain_dir (solo project_id) se
        # usa el mismo repo canonico directamente.
        if chain_dir:
            from features.cognitive_knowledge.application.use_cases import (
                SaveToCkgUseCase,
            )
            from returns.pipeline import is_successful

            result = SaveToCkgUseCase.execute(
                chain_dir,
                [node],
                task_id=ctx.task_id or "",
                session_id=ctx.session_id or "",
            )
            if not is_successful(result):
                return TaskResult.failure(str(result.failure()))
            res = result.value_or({})
            nodes_saved = res.get("nodes_saved", 0)
            degraded = (res.get("result", {}) or {}).get("degraded", [])
            ok = bool(res.get("ok", False))
        else:
            ckg = ckg_service(project_root)
            res = ckg.save_nodes([node])
            nodes_saved = res.get("nodes_created", 0)
            degraded = res.get("degraded", [])
            ok = bool(res.get("ok", False))

        return TaskResult.success(
            data={
                "ok": ok,
                "nodes_saved": nodes_saved,
                "degraded": degraded,
                "note": (
                    "La nueva decision reemplaza a las previas; estas quedaron "
                    "con status=superseded y solo se ven con include_superseded."
                    if supersedes
                    else "Decision activa registrada."
                ),
                "next_step": "Run 'walker <project>/kg_search' to verify saved knowledge",
            }
        )
    except Exception as e:
        return TaskResult.failure(
            str(e),
            data={
                "error": f"CKG write failed: {e}",
                "next_step": "Run 'walker <project>/build_initial_ckg' to initialize CKG first",
            },
        )


def kg_agent_context(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    from features.task_execution.tasks.kg_agent_context import kg_agent_context as _kg_agent_context
    return _kg_agent_context(ctx, data)


def register(registry: Any) -> None:
    for name, fn, meta in [
        (
            "kg_schema",
            kg_schema,
            {
                "description": "Descubre indices disponibles o schema de un indice especifico",
                "usage": "walker <project>/kg_schema",
                "example": 'walker <project>/kg_schema \'{"index":"delta"}\'',
                "query_instructions": (
                    "Sin body: lista todos los indices disponibles. "
                    'Con body {"index":"delta"}: muestra los campos y tipos de ese indice.'
                ),
                "tags": ["kg", "cognitive-graph"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "string", "description": "Índice del que se quiere el schema (delta, ckg, cpg, trace). Sin body devuelve lista de índices", "example": "delta"},
                    },
                    "required": [],
                },
            },
        ),
        (
            "kg_search",
            kg_search,
            {
                "description": "Busca en un indice de CognitiveGraph (delta, ckg, cpg, trace)",
                "usage": 'walker <project>/kg_search \'{"campo":"valor"}\'',
                "example": 'walker <project>/kg_search \'{"_index":"delta","text":"authentication"}\'',
                "query_instructions": (
                    'Body es la query JSON. Usa {"text":"..."} para full-text. '
                    'Usa {"campo":"valor"} para busqueda estructurada. '
                    "Con _index eliges el indice (default ckg). Primero usa kg_schema para ver campos."
                ),
                "tags": ["kg", "cognitive-graph"],
            },
        ),
        (
            "save_to_ckg",
            save_to_ckg,
            {
                "description": "Almacena conocimiento en CKG (use case SaveToCkgUseCase). Acepta 'nodes' (workflow) o statement/label con ciclo de vida: con 'supersedes' (lista de statements o IDs) degrada a las decisiones anteriores reemplazadas.",
                "usage": 'walker <project>/save_to_ckg \'{"type":"decision","statement":"...","supersedes":["<statement o id previo>"]}\'',
                "tags": ["kg", "cognitive-graph"],
            },
        ),
        (
            "kg_multi",
            kg_multi,
            {
                "description": "Query federada en multiples indices a la vez",
                "usage": '<walker <project>/kg_multi \'{"delta":{},"ckg":{}}\'',
                "example": 'walker <project>/kg_multi \'{"delta":{"text":"auth"},"cpg":{"file":"src/auth.py"}}\'',
                "query_instructions": (
                    "Cada clave del body es un indice (delta, ckg, cpg, trace) y cada valor "
                    "es la query para ese indice. Recibe resultados de todos en una sola respuesta."
                ),
                "tags": ["kg", "cognitive-graph"],
                "input_schema": {
                    "type": "object",
                    "description": "Cada clave del body es un índice (delta, ckg, cpg, trace); cada valor es la query para ese índice.",
                    "examples": [{"delta": {"text": "auth"}, "ckg": {}}],
                    "additionalProperties": {"type": "object"},
                },
            },
        ),
        (
            "agent_context",
            kg_agent_context,
            {
                "description": "Consulta consolidada delta+ckg+cpg+trace del proyecto en una sola llamada",
                "usage": 'walker <project>/agent_context \'{"delta":{},"ckg":{},"cpg":{},"trace":{}}\'',
                "example": 'walker <project>/agent_context \'{"delta":{"text":"auth"},"ckg":{"scope":"security"}}\'',
                "query_instructions": (
                    "Cada clave del body es una query para ese indice. "
                    "Devuelve un resumen por sección: sprint/tasks (delta), "
                    "ADRs/convenciones (ckg), archivos/símbolos (cpg), "
                    "eventos recientes (trace)."
                ),
                "tags": ["kg", "context", "agent"],
            },
        ),
    ]:
        registry.register(name, fn, metadata=meta)
