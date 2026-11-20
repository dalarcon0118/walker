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
    # contra el trace. La entrada (node, sub_state) es el token: sin ella el
    # gate sólo podía comparar timestamps y cualquier submit obligaba a
    # recargar (P1.1). Fail-open: si el estado no se puede leer, el evento va
    # sin posición y el gate cae al fallback por timestamp.
    node, sub_state = _workflow_position(ctx.chain_dir)
    event = {
        "event_type": "skill_loaded",
        "source": "agent",
        "tool": "load_skill",
        "task_id": str(kwargs.get("task_id") or ""),
        "data": {"skill": skill.name, "scope": skill.scope,
                 "node": node, "sub_state": sub_state},
    }
    warning = _trace_skill_load(ctx.chain_dir, event)
    result = {
        "name": skill.name,
        "description": skill.description,
        "scope": skill.scope,
        "path": str(skill.path),
        "chars": len(body),
        "content": body,
    }
    if warning:
        result["warning"] = warning
    return result


#: Cache corto del health de memoria (por data_dir). El CPG se carga completo
#: para contar cyclo; hacerlo en cada `agent_context` sería caro y el estado
#: cambia lento (sólo al reindexar).
_HEALTH_CACHE: dict[str, tuple[float, dict]] = {}
_HEALTH_TTL = 60.0


def _memory_health(chain_dir: str) -> dict:
    """Salud de CKG/CPG + cobertura de cyclo (P1.2).

    Los health checks ya existían (`CkgReadService.check_health`,
    `CheckCpgHealthUseCase`) pero no los consumía ninguna decisión: el agente
    citaba `adr-###` de un CKG vacío como si fuera autoridad. `trusted` es la
    señal para no citar memoria degradada; `cyclo.status` distingue `partial`
    (extractor incompleto) de `unavailable` (no hay nada que medir).
    """
    out: dict[str, Any] = {
        "ckg": {"nodes": 0, "status": "unknown"},
        "cpg": {"nodes": 0, "status": "unknown"},
        "cyclo": {"known": 0, "total": 0, "status": "unknown"},
        "trusted": False,
    }
    if not chain_dir:
        return out
    try:
        from fast_mcp.shared_kernel.project_support import ProjectService

        data_dir = str(ProjectService.resolve_paths(chain_dir).data_dir or "")
    except Exception as exc:  # noqa: BLE001 — sin data_dir no hay health
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    if not data_dir:
        return out

    # Health es observación de estado lento: TTL corto para no pagar el load
    # del CPG (8.8k nodos) en cada `agent_context`, que es la tool más llamada.
    import time

    hit = _HEALTH_CACHE.get(data_dir)
    if hit and time.monotonic() - hit[0] < _HEALTH_TTL:
        return dict(hit[1])

    try:
        from returns.pipeline import is_successful

        from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import (
            CkgReadService,
        )

        res = CkgReadService.check_health(data_dir)
        if is_successful(res):
            data = res.value_or({}) or {}
            nodes = int(data.get("node_count") or 0)
            out["ckg"] = {"nodes": nodes, "status": "ok" if nodes > 0 else "empty"}
        else:
            out["ckg"] = {"nodes": 0, "status": "error"}
    except Exception as exc:  # noqa: BLE001 — health es observación, no gate
        out["ckg"] = {"nodes": 0, "status": "error",
                      "error": f"{type(exc).__name__}: {exc}"}

    try:
        from fast_mcp.features.code_analysis.application.use_cases.check_cpg_health import (
            CheckCpgHealthUseCase,
        )

        cpg = CheckCpgHealthUseCase.execute(data_dir) or {}
        nodes = int(cpg.get("node_count") or 0)
        out["cpg"] = {"nodes": nodes, "status": "ok" if nodes > 0 else "empty"}
        cyclo = cpg.get("cyclo") or {}
        out["cyclo"] = {
            "known": int(cyclo.get("known") or 0),
            "total": int(cyclo.get("total") or 0),
            "status": str(cyclo.get("status") or "unknown"),
            "by_language": dict(cyclo.get("by_language") or {}),
        }
    except Exception as exc:  # noqa: BLE001 — health es observación, no gate
        out["cpg"] = {"nodes": 0, "status": "error",
                      "error": f"{type(exc).__name__}: {exc}"}

    out["trusted"] = out["ckg"]["status"] == "ok" and out["cpg"]["status"] == "ok"
    _HEALTH_CACHE[data_dir] = (time.monotonic(), dict(out))
    return out


def _workflow_position(chain_dir: str) -> tuple[str, str]:
    """(node, sub_state) actual del workflow, o ("", "") si no se puede leer.

    Es el token de P1.1: `load_skill` firma en qué entrada se cargó el skill.
    Fail-open a propósito — una tool de memoria no puede romperse por no poder
    leer el estado; en ese caso el gate cae al fallback por timestamp.
    """
    if not chain_dir:
        return "", ""
    try:
        from fast_mcp.features.workflow.context import WorkflowContext

        wctx = WorkflowContext.resolve(chain_dir)
        state = wctx.orchestrator.current_state() if wctx is not None else None
        if state is None:
            return "", ""
        return (
            str(getattr(state, "current_node", "") or ""),
            str(getattr(state, "current_sub_state", "") or ""),
        )
    except Exception:  # noqa: BLE001 — sin estado, el token queda vacío
        return "", ""


def _trace_skill_load(chain_dir: str, event: dict) -> str:
    """Empuja `skill_loaded` y verifica que quedó. Devuelve warning o "".

    Un reintento con servicio recién construido cubre la instancia cacheada que
    rechaza el write por generación (misma clase de fallo que ya documenta
    `trace_factory`). Si aun así no escribe, se reporta: nunca silencio.
    """
    try:
        from fast_mcp.features.event_tracing.application.services.trace_factory import (
            build_trace_service,
            invalidate_trace_service,
        )
    except Exception as exc:  # noqa: BLE001
        return f"no se pudo registrar la carga del skill: {type(exc).__name__}: {exc}"

    error = ""
    for attempt in (1, 2):
        service = build_trace_service(chain_dir)
        if service is None:
            error = "TraceGraph no disponible"
        else:
            try:
                pushed = service.push_event(chain_dir, event)
            except Exception as exc:  # noqa: BLE001
                pushed = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            if isinstance(pushed, dict) and pushed.get("ok", True):
                return ""
            error = str((pushed or {}).get("error") or "escritura rechazada")
        if attempt == 1:
            # La instancia cacheada puede estar rechazando por generación:
            # se descarta y se reconstruye antes del reintento.
            try:
                invalidate_trace_service(chain_dir)
            except Exception:  # noqa: BLE001
                pass
    return f"no se pudo registrar la carga del skill: {error}"


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
    # P1.2: salud de la memoria como precondición de la decisión. `trusted`
    # es la señal que el plugin usa para no citar `adr-###` de un CKG vacío.
    out["memory_health"] = _memory_health(ctx.chain_dir)

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


def merge_write_evidence(flight: dict, chain_dir: str, scanner=None) -> dict:
    """Completa `wrote/writes/touched` del flight state con el diff del repo.

    Solo actúa si el trace NO trae `wrote`: lo observado por el plugin es más
    preciso (sabe qué herramienta escribió). Esto cubre la escritura que el
    hook no vio (subagente, shell del host, `sed -i`, `git apply`): el cierre
    queda con deuda en vez de pasar limpio.
    """
    # Sin estado de vuelo no se escanea: el primer `session_context` de una
    # sesión nueva vería cambios previos del repo y le inventaría deuda.
    if not flight or flight.get("wrote") or not chain_dir:
        return flight
    try:
        from fast_mcp.shared_kernel.project_support.watcher import (
            changed_since_last_scan,
        )

        changed = (scanner or changed_since_last_scan)(chain_dir)
    except Exception:  # noqa: BLE001 — el contexto no depende del scan
        changed = []
    if changed:
        flight["wrote"] = True
        flight["writes"] = max(int(flight.get("writes") or 0), len(changed))
        flight["touched"] = list(changed)[:20]
        flight["touched_source"] = "watcher"
    return flight


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

    # 0. REGISTRO: ciclo de vida y jerarquía de la sesión (si existe)
    try:
        from fast_mcp.composition import build_sprint_service

        reg = build_sprint_service(ctx.chain_dir)
        record = reg.get_session(sid) if reg is not None else None
        out["session"] = record or {"status": "unregistered"}
    except Exception as exc:  # noqa: BLE001
        out["session_error"] = f"{type(exc).__name__}: {exc}"

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

    # 2b. SPRINT DE LA SESIÓN (no el default del proyecto) + jerarquía.
    # `get_active_spring()` devuelve el sprint más reciente del PROYECTO, que
    # puede ser de otra sesión; acá se ancla en el sprint que la sesión posee.
    try:
        from fast_mcp.composition import build_sprint_service

        record = out.get("session") or {}
        spring_id = str(record.get("spring_id") or "")
        project_id = str(record.get("project_id") or "")
        svc = build_sprint_service(ctx.chain_dir)
        if spring_id and svc is not None:
            spring = svc.get_spring(spring_id) or {}
            out["sprint"] = {
                "spring_id": spring_id,
                "goal": spring.get("goal") or record.get("goal") or "",
                "status": spring.get("status") or "",
            }
        if project_id and svc is not None:
            out["children"] = [
                s.get("session_id")
                for s in svc.list_sessions(project_id=project_id)
                if (s.get("parent_session_id") or "") == sid
            ]
    except Exception as exc:  # noqa: BLE001
        out["sprint_error"] = f"{type(exc).__name__}: {exc}"

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

    # 3b. FLIGHT STATE: proyección del último estado de vuelo de la sesión.
    # El TRACE es el SSOT; el último evento `session_state` (o, si no hay,
    # el último `session_metrics`) describe verified/verified_at/verify_level/
    # dirty_since/wrote/writes/touched. Se usa la lista completa de eventos,
    # no el slice de display.
    _FLIGHT_KEYS = (
        "verified", "verified_at", "verify_level", "dirty_since",
        "wrote", "writes", "touched",
    )
    _source = None
    for _e in events:
        if getattr(_e, "event_type", None) == "session_state":
            _source = _e
    if _source is None:
        for _e in events:
            if getattr(_e, "event_type", None) == "session_metrics":
                _source = _e
    if _source is not None:
        _data = getattr(_source, "data", None) or {}
        if not isinstance(_data, dict):
            _data = {}
        out["flight_state"] = {k: _data[k] for k in _FLIGHT_KEYS if k in _data}
    else:
        out["flight_state"] = {}

    # 3c. Evidencia de escritura SIN hook (P1.12): diff contra el último scan.
    # Completa solo lo que el trace no trae: si el plugin ya observó `wrote`,
    # ese dato es más preciso (sabe QUÉ herramienta escribió). Sin esto, una
    # sesión que editó por un camino no observado cerraba limpia sin deuda.
    out["flight_state"] = merge_write_evidence(
        out.get("flight_state") or {}, ctx.chain_dir
    )

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


def handle_preflight(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Preflight real: qué se toca, a quién afecta y qué lo verifica.

    Corre ANTES de escribir. Para cada archivo declarado devuelve impacto
    (llamadores, llamadas, imports, complejidad), los tests que lo mencionan y
    las decisiones del CKG; y una foto del baseline del scope. Las partes que no
    se pueden derivar se declaran en `unmapped`.
    """
    body = kwargs.get("_payload", kwargs) or {}
    files = [str(f) for f in (body.get("files") or kwargs.get("files") or []) if f]
    if not files:
        return {"error": "files requerido: el preflight es por archivos", "type": "preflight"}

    out: dict[str, Any] = {"type": "preflight", "files": [], "unmapped": []}
    out["memory_health"] = _memory_health(ctx.chain_dir)

    # 1. Impacto por archivo
    for f in files[:10]:
        entry: dict[str, Any] = {"file": f}
        try:
            code = ctx.code_context_service().execute(target_file=f, query="")
            rel = code.get("relations") or {}
            syms = [s for s in (code.get("symbols") or []) if s.get("cyclo")]
            syms.sort(key=lambda s: -(s.get("cyclo") or 0))
            entry.update({
                "symbols": len(code.get("symbols") or []),
                "callers": rel.get("called_by_total", len(rel.get("called_by") or [])),
                "calls": rel.get("calls_total", len(rel.get("calls") or [])),
                "imports": len(rel.get("imports") or []),
                "heaviest": [{"name": s.get("name"), "cyclo": s.get("cyclo")} for s in syms[:3]],
                "top_callers": [c.get("name") for c in (rel.get("called_by") or [])[:5]],
            })
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"{type(exc).__name__}: {exc}"
        out["files"].append(entry)

    # 2. Tests que cubren esos archivos (heuristic, se declara)
    try:
        from fast_mcp.features.tasks.handlers.spec_derive import tests_for_files

        tests = tests_for_files(files, ctx.chain_dir)
        out["tests"] = tests
        out["tests_source"] = "nombre + contenido (heurística)"
        if not tests:
            out["unmapped"].append({
                "criterion": "los tests del área tocada pasan",
                "reason": "no se encontraron tests asociados a los archivos",
            })
    except Exception as exc:  # noqa: BLE001
        out["unmapped"].append({"criterion": "tests", "reason": f"{type(exc).__name__}: {exc}"})

    # 3. Baseline del scope común (o del primero si no comparten prefijo)
    try:
        import os

        from fast_mcp.features.code_analysis.application.baseline_service import (
            BaselineService,
        )

        dirs = {os.path.dirname(f) for f in files}
        scope = next(iter(dirs)) + "/" if len(dirs) == 1 else ""
        snap = BaselineService(ctx.chain_dir).capture(scope=scope, light=True)
        out["baseline"] = {
            "scope": scope,
            "structure": snap.get("structure"),
            "complexity": {k: v for k, v in (snap.get("complexity") or {}).items() if k != "top"},
            "dimensions": snap.get("dimensions"),
        }
    except Exception as exc:  # noqa: BLE001
        out["unmapped"].append({"criterion": "baseline", "reason": f"{type(exc).__name__}: {exc}"})

    # 4. Decisiones del CKG del scope
    try:
        from returns.pipeline import is_successful

        from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import (
            CkgReadService,
        )

        query = " ".join(f.split("/")[-1].replace(".py", "") for f in files[:3])
        res = CkgReadService.gather_context(ctx.chain_dir, query=query)
        hits = res.value_or([]) if is_successful(res) else []
        out["ckg"] = {"hits": len(hits), "statements": [
            str(h.get("statement") or h.get("label") or "")[:120] for h in hits[:5]
        ]}
    except Exception as exc:  # noqa: BLE001
        out["ckg"] = {"error": f"{type(exc).__name__}: {exc}"}

    out["answerable"] = all("error" not in f for f in out["files"])
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
