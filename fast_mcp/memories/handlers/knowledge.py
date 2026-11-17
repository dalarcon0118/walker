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

    # `session_id`/`task_id` viajan al CKG: sin ellos una decisión no se puede
    # correlacionar con la sesión que la tomó (la capacidad existía en el
    # servicio y la tool no la usaba).
    session_id = str(payload.get("session_id", "") or kwargs.get("session_id", "") or "")
    task_id = str(payload.get("task_id", "") or kwargs.get("task_id", "") or "")

    if isinstance(nodes, list) and nodes:
        result = CkgWriteService.save_nodes(
            ctx.chain_dir, nodes, task_id=task_id, session_id=session_id
        )
        return result.value_or({}) if is_successful(result) else {"error": str(result.failure())}

    node = build_ckg_node(payload)
    if session_id:
        (node.setdefault("metadata", {}) or {}).setdefault("source", f"session:{session_id}")
    result = CkgWriteService.save_nodes(
        ctx.chain_dir, [node], task_id=task_id, session_id=session_id
    )
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
    # Traza la carga: el gate `skill_intent.policy == required` la verifica
    # contra el trace (task + timestamp). Best-effort: sin grafo no se bloquea.
    try:
        from fast_mcp.features.event_tracing.application.services.trace_factory import (
            build_trace_service,
        )

        service = build_trace_service(ctx.chain_dir)
        if service is not None:
            service.push_event(ctx.chain_dir, {
                "event_type": "skill_loaded",
                "source": "agent",
                "tool": "load_skill",
                "task_id": str(kwargs.get("task_id") or ""),
                "data": {"skill": skill.name, "scope": skill.scope},
            })
    except Exception as exc:  # noqa: BLE001 — la carga del skill no depende del trace
        logger.debug("load_skill: no se pudo trazar (%s)", exc)
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


def _resolve_anchor(
    ctx: MemoryContext, task_id: str, target_file: str, query: str
) -> tuple[dict, list[str], dict | None]:
    """Decide el ancla del contexto: delta (tarea) → archivo → texto.

    Devuelve (anchor, files_de_la_tarea, spec). `anchor["reason"]` explica el
    fallback para que el agente sepa qué NO se pudo consultar.
    """
    if task_id:
        spec = _find_task_spec(ctx.chain_dir, task_id)
        if spec:
            files = [f for f in (spec.get("files") or []) if isinstance(f, str)]
            return (
                {"type": "task", "value": task_id, "resolved": True,
                 "files": files[:10]},
                files,
                {k: spec.get(k) for k in (
                    "spec_id", "objective", "status", "description",
                    "acceptance_criteria", "preconditions", "predicates",
                )},
            )
        reason = "task_id no está en el delta (sin sprint/spec con ese id)"
        if target_file:
            return {"type": "file", "value": target_file, "resolved": True, "reason": reason}, [], None
        if query:
            return {"type": "text", "value": query, "resolved": True, "reason": reason}, [], None
        return {"type": "none", "value": "", "resolved": False, "reason": reason}, [], None

    if target_file:
        return {"type": "file", "value": target_file, "resolved": True}, [], None
    if query:
        return {"type": "text", "value": query, "resolved": True}, [], None
    return {
        "type": "none", "value": "", "resolved": False,
        "reason": "sin task_id, file ni text: no hay ancla para el contexto",
    }, [], None


def _find_task_spec(chain_dir: str, task_id: str) -> dict | None:
    """Busca la spec de la tarea en el delta. None si no hay sprint o no está."""
    try:
        # `SprintService.from_chain_dir` requiere un resolver inyectado y sin
        # él devolvía None: TODO task_id parecía inexistente. El composition
        # root expone el builder con su repo.
        from fast_mcp.composition import build_sprint_service

        service = build_sprint_service(chain_dir)
        if service is None:
            return None
        # `get_active_spring` devuelve None si el sprint está en `planning`
        # (no "active"), así que una tarea recién creada nunca se encontraba.
        # Se busca en el sprint activo y, si no hay, en todos.
        springs = []
        active = service.get_active_spring()
        if active:
            springs.append(active)
        springs.extend(service.list_springs() or [])
        seen: set[str] = set()
        for spring in springs:
            spring_id = str(spring.get("spring_id") or spring.get("id") or "")
            if not spring_id or spring_id in seen:
                continue
            seen.add(spring_id)
            for spec in service.get_specs(spring_id) or []:
                # `implement_task` guarda el id real en `task_context.task_id`
                # (el spec_id es autogenerado: SPEC-00N).
                ctx_data = spec.get("task_context") or {}
                if task_id in (
                    str(spec.get("spec_id", "")),
                    str(spec.get("objective", "")),
                    str(ctx_data.get("task_id", "")),
                ):
                    return spec
            found = service.get_spec(spring_id, task_id)
            if found:
                return found
        return None
    except Exception:  # noqa: BLE001 — sin delta el ancla cae al archivo
        return None


def handle_agent_context(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Contexto consolidado: CKG + CPG (impacto) + TRACE.

    Devolvía SOLO CKG, aunque el contrato dice "delta+ckg+cpg+trace con
    cross-references". Era el tool que el agente más llama y la mitad de la
    navegación (a qué afecta el cambio) nunca llegaba.
    """
    from returns.pipeline import is_successful

    from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import CkgReadService

    body = kwargs.get("_payload", kwargs) or {}
    ckg_q = body.get("ckg", {}) or {}
    query = ckg_q.get("text", ckg_q.get("query", "")) or body.get("text", "") or ""
    target_file = body.get("file", "") or kwargs.get("file", "")
    task_id = str(body.get("task_id", "") or kwargs.get("task_id", "") or "")

    # ANCLA en cascada: la tarea (delta) manda porque es lo que el agente está
    # trabajando; si no hay tarea registrada se cae al archivo y después al
    # texto. La respuesta dice SIEMPRE en qué se ancló: un contexto vacío sin
    # explicación se lee como "no hay nada que saber" y no es lo mismo que
    # "no encontré el ancla".
    anchor, anchor_files, task_spec = _resolve_anchor(ctx, task_id, target_file, query)
    if not target_file and anchor_files:
        target_file = anchor_files[0]

    out: dict[str, Any] = {
        "type": "agent_context",
        "project_id": kwargs.get("_project_id", ctx.project_id),
        "anchor": anchor,
        "task": task_spec,
        "file": target_file,
        "text": query,
    }

    # CKG: decisiones y convenciones del scope.
    result = CkgReadService.gather_context(ctx.chain_dir, query=query or target_file)
    out["ckg"] = {"results": result.value_or([]) if is_successful(result) else []}

    # CPG: qué toca este archivo/símbolo (llamadas, llamadores, imports).
    # Si el ancla fue una tarea, se usa su primer archivo declarado.
    if target_file or query:
        try:
            cpg = ctx.code_context_service().execute(
                target_file=target_file, query="" if target_file else query
            )
            # Recortar la lista de relaciones: 30 llamadores con 3 campos cada
            # uno son ~1k tokens de ruido. Se informa el total y se dejan 10.
            rel = cpg.get("relations") if isinstance(cpg, dict) else None
            if isinstance(rel, dict):
                for key, items in list(rel.items()):
                    if isinstance(items, list) and len(items) > 10:
                        rel[key] = items[:10]
                        rel[f"{key}_total"] = len(items)
            out["cpg"] = cpg
        except Exception as exc:  # noqa: BLE001
            out["cpg"] = {"error": f"{type(exc).__name__}: {exc}"}

    # TRACE: los últimos eventos del proyecto (intentos previos, fallos).
    try:
        from fast_mcp.features.event_tracing.application.services.trace_factory import (
            build_trace_service,
        )

        service = build_trace_service(ctx.chain_dir)
        if service is not None:
            events = service.load_traces(ctx.chain_dir)
            if task_id:
                filtered = [e for e in events if task_id in str(e.task_id or "")]
                events = filtered or events
            events = events[-8:]
            out["trace"] = [
                {"event": e.event_type, "at": (e.timestamp or "")[:19], "data": e.data}
                for e in events
            ]
    except Exception:  # noqa: BLE001
        pass

    return out


def handle_session_context(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Todo lo que pasó en una sesión, unido por `session_id`.

    El id es la clave de join entre los cuatro grafos: el TRACE lo guarda en
    metadata, el DELTA en `task_context.session_id`, el CKG en
    `metadata.source = "session:<id>"` y el CPG se alcanza por los archivos
    tocados. Devuelve las cuatro vistas juntas y declara cuál quedó vacía.
    """
    body = kwargs.get("_payload", kwargs) or {}
    sid = str(body.get("session_id", "") or kwargs.get("session_id", "") or "").strip()
    if not sid:
        return {"error": "session_id requerido", "type": "session_context"}

    out: dict[str, Any] = {"type": "session_context", "session_id": sid}

    # 1. TRACE: qué hizo el agente
    events: list = []
    try:
        from fast_mcp.features.event_tracing.application.services.trace_factory import (
            build_trace_service,
        )

        service = build_trace_service(ctx.chain_dir)
        if service is not None:
            events = [
                e for e in service.load_traces(ctx.chain_dir)
                if str((e.metadata or {}).get("session_id", "")) == sid
            ]
    except Exception as exc:  # noqa: BLE001
        out["trace_error"] = f"{type(exc).__name__}: {exc}"

    out["trace"] = {
        "count": len(events),
        "types": sorted({e.event_type for e in events}),
        "events": [
            {"event": e.event_type, "at": (e.timestamp or "")[:19],
             "task_id": e.task_id, "files": e.files, "data": e.data}
            for e in events[-30:]
        ],
    }

    # 2. DELTA: qué tareas se hicieron
    tasks: list[dict] = []
    try:
        from fast_mcp.composition import build_sprint_service

        service = build_sprint_service(ctx.chain_dir)
        if service is not None:
            seen: set[str] = set()
            springs = ([service.get_active_spring()] if service.get_active_spring() else [])
            springs += service.list_springs() or []
            for spring in springs:
                spring_id = str(spring.get("spring_id") or spring.get("id") or "")
                if not spring_id or spring_id in seen:
                    continue
                seen.add(spring_id)
                for spec in service.get_specs(spring_id) or []:
                    if str((spec.get("task_context") or {}).get("session_id", "")) == sid:
                        tasks.append({
                            "spec_id": spec.get("spec_id"),
                            "objective": spec.get("objective"),
                            "status": spec.get("status"),
                            "files": spec.get("files") or [],
                        })
    except Exception as exc:  # noqa: BLE001
        out["delta_error"] = f"{type(exc).__name__}: {exc}"

    out["tasks"] = tasks

    # 3. Archivos tocados: trace + tareas declaradas
    files: list[str] = []
    for e in events:
        for f in (e.files or []):
            if f and f not in files:
                files.append(f)
    for t in tasks:
        for f in (t.get("files") or []):
            if f and f not in files:
                files.append(f)
    out["files"] = files[:50]

    # 4. CKG: decisiones y hechos de la sesión
    try:
        from returns.pipeline import is_successful

        from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import (
            CkgReadService,
        )

        # El vínculo está en `metadata.session_id`, no en el texto: buscar
        # "session:<id>" por FTS no encuentra nada. Se filtra exacto sobre el
        # CKG del proyecto (es chico: cientos de nodos, no millones).
        all_nodes = CkgReadService.query_all(ctx.chain_dir)
        by_session = [
            n for n in all_nodes
            if str((n.get("metadata") or {}).get("session_id", "")) == sid
        ]
        if by_session:
            out["ckg"] = {"results": by_session[:20], "matched": "metadata.session_id"}
        else:
            result = CkgReadService.gather_context(ctx.chain_dir, query=f"session:{sid}")
            out["ckg"] = {
                "results": result.value_or([]) if is_successful(result) else [],
                "matched": "text",
            }
    except Exception as exc:  # noqa: BLE001
        out["ckg"] = {"error": f"{type(exc).__name__}: {exc}"}

    # 5. CPG: forma de los archivos tocados (máx 3, para no explotar el tamaño)
    cpg: list[dict] = []
    for f in files[:3]:
        try:
            code = ctx.code_context_service().execute(target_file=f, query="")
            rel = code.get("relations") or {}
            cpg.append({
                "file": f,
                "symbols": len(code.get("symbols") or []),
                "calls": rel.get("calls_total", len(rel.get("calls") or [])),
                "called_by": rel.get("called_by_total", len(rel.get("called_by") or [])),
            })
        except Exception:  # noqa: BLE001
            continue
    out["cpg"] = cpg

    out["empty_layers"] = [
        name for name, value in (
            ("trace", out["trace"]["count"]),
            ("delta", len(tasks)),
            ("ckg", len((out.get("ckg") or {}).get("results") or [])),
            ("cpg", len(cpg)),
        ) if not value
    ]
    return out


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
