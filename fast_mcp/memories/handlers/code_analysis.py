"""fast_mcp/memories/handlers/code_analysis.py — Code analysis tool handlers.

Each handler receives a resolved MemoryContext (no repeated resolve_paths)
and returns a dict. Consistent error handling via the dispatch() wrapper.
"""
from __future__ import annotations

import os
from typing import Any

from fast_mcp.memories.context import MemoryContext


def handle_read_code(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Read a project file's content by path."""
    file_path = kwargs.get("file", "")
    if not file_path:
        return {"error": "file is required"}

    candidate = (
        file_path if os.path.isabs(file_path)
        else os.path.join(ctx.project_root, file_path)
    )
    candidate = os.path.normpath(candidate)

    if not os.path.isfile(candidate):
        return {"file": file_path, "exists": False, "error": f"file not found: {file_path}"}

    with open(candidate, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    return {
        "file": file_path,
        "project_root": ctx.project_root,
        "exists": True,
        "size": len(content),
        "content": content,
    }


def handle_code_search(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Búsqueda estructurada en el CPG (memoria del código).

    DSL de QueryGraphUseCase.grep: texto libre, function:name file:path,
    trace:funcA->funcB (call path), calls:fn (relations), bug:* (dead
    branches, missing returns, taint paths...), security:* (taint flows,
    hardcoded secrets...).
    """
    query = kwargs.get("query", "").strip()
    if not query:
        return {"error": "query is required"}

    from fast_mcp.features.code_analysis.application.query_service import QueryGraphUseCase
    from fast_mcp.shared_kernel.project_support import ProjectService

    # data_dir canónico explícito (ADR-054 D4 / fix R-A) — la herramienta
    # busca en la memoria CPG del proyecto, no en una ruta adivinada.
    paths = ProjectService.resolve_paths(ctx.chain_dir)
    use_case = QueryGraphUseCase(
        chain_dir=ctx.chain_dir,
        project_id=paths.project_id or "",
        data_dir=str(paths.data_dir) if paths.data_dir else None,
    )
    result = use_case.grep(query)
    if not isinstance(result, dict):
        return {"query": query, "count": 0, "results": [], "source": "cpg", "result": result}

    max_results = int(kwargs.get("max_results", 20))
    matches = result.get("matches", [])
    return {
        "query": query,
        "count": len(matches),
        "results": matches[:max_results],
        "total": result.get("total", len(matches)),
        "source": "cpg",
        "result": result,
    }


def handle_code_context(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Structured code context: symbols, calls, imports and relations."""
    service = ctx.code_context_service()
    target_file = kwargs.get("file", "")
    query = kwargs.get("query", "").strip()
    return service.execute(target_file=target_file, query=query)


def _review_targets(project_root: str, kwargs: dict[str, Any]) -> list[str] | None:
    """Archivos a revisar: `files` explícito o `scope` cuando apunta a un archivo.

    El branch `review` ignoraba ambos y corría el review sobre TODO el repo:
    las reglas del CPG nunca se ejecutaban sobre los archivos de la tarea.
    """
    files = kwargs.get("files")
    if isinstance(files, list):
        clean = [str(f) for f in files if str(f).strip()]
        if clean:
            return clean
    scope = str(kwargs.get("scope") or kwargs.get("area") or "").strip()
    if scope:
        candidate = scope if os.path.isabs(scope) else os.path.join(project_root, scope)
        if os.path.isfile(candidate):
            return [scope]
    return None


def handle_inspect_codebase(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Inspect codebase: architecture, dead code, security, metrics."""
    service = ctx.code_analysis_service()
    action = kwargs.get("action", "business")
    scope = kwargs.get("scope", kwargs.get("area", ctx.chain_dir))

    dispatch_map = {
        "business": lambda: service.business_layer(focus=scope),
        "dead_code": lambda: service.detect_unused_functions(file_filter=scope),
        "review": lambda: service.review_code(files=_review_targets(ctx.project_root, kwargs)),
        "metrics": lambda: service.project_metrics(file_filter=scope),
        # CFG/DFG como ciudadanos de primera (antes: solo via DSL code_search;
        # action=security estaba documentada en el schema y devolvía Unknown).
        "bugs": lambda: service.find_bugs(
            bug_type=kwargs.get("bug_type", "taint_paths"), file_filter=scope
        ),
        "security": lambda: service.security_scan(
            vuln_type=kwargs.get("vuln_type", "all"), file_filter=scope
        ),
        # W3: DFG/CFG consultables (P-1/P-2 ADR-076).
        "dataflow": lambda: service.dataflow(file=scope, symbol=kwargs.get("symbol", "")),
        "controlflow": lambda: service.controlflow(file=scope, symbol=kwargs.get("symbol", "")),
        # W4: DSL trace/relations del CPG (P-3 ADR-076).
        "query": lambda: service.query(query=kwargs.get("query", "")),
        # W7: convenciones del CKG contra el código (CKG↔CPG circuit).
        "conventions": lambda: service.check_conventions(languages=kwargs.get("languages")),
        # W8: health del CPG (index status, node count).
        "health": lambda: service.check_health(),
    }

    result_fn = dispatch_map.get(action)
    if result_fn is None:
        return {
            "scope": scope,
            "action": action,
            "error": f"Unknown action: {action}",
            "actions": sorted(dispatch_map),
        }

    return {"scope": scope, "action": action, "result": result_fn()}


def handle_build_code_index(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Full CPG rebuild from scratch."""
    cpg = ctx.cpg()
    if cpg is None:
        return {"built": False, "error": "CPG not available"}
    result = cpg.build()
    return {"built": True, "project_root": ctx.project_root, "result": result}


def handle_update_incremental_code(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Incremental CPG update via watcher: re-indexes only changed files."""
    body = kwargs.get("_payload", kwargs) or {}
    bug_proposals = bool(body.get("bug_proposals", True))
    cpg = ctx.cpg()
    if cpg is None:
        return {"updated": False, "error": "CPG not available"}
    result = cpg.update(bug_proposals=bug_proposals)
    # Fail-fast honesto: un update abortado NO se reporta como exitoso.
    updated = bool(result.get("ok", True)) if isinstance(result, dict) else True
    return {"updated": updated, "project_root": ctx.project_root, "result": result,
            "bug_proposals": bug_proposals}


def handle_snapshot(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Captura el estado del scope (punto A): estructura, relaciones, firmas,
    complejidad, snapshot de archivos, convenciones y defectos.

    Con `label` se persiste en `<data_dir>/baselines/<label>.json`; sin label
    solo se devuelve. Las dimensiones que no se pueden computar se marcan
    `unavailable` con el motivo — nunca un cero que parece un dato.
    """
    from fast_mcp.features.code_analysis.application.baseline_service import BaselineService

    body = kwargs.get("_payload", kwargs) or {}
    service = BaselineService(ctx.chain_dir)
    return service.capture(
        scope=str(body.get("scope", "") or ""),
        label=str(body.get("label", "") or ""),
    )


def handle_baseline_diff(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Compara un baseline guardado contra el estado actual del mismo scope."""
    from fast_mcp.features.code_analysis.application.baseline_service import BaselineService

    body = kwargs.get("_payload", kwargs) or {}
    label = str(body.get("base", body.get("label", "")) or "")
    if not label:
        return {"error": "falta 'base' (label del snapshot)", "available": BaselineService(ctx.chain_dir).list_labels()}
    return BaselineService(ctx.chain_dir).diff(label=label, scope=str(body.get("scope", "") or ""))
