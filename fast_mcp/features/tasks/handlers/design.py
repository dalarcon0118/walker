"""handlers/design.py — acciones del nodo DESIGN.

`gather_context` (DESIGN_GATHER) y `documenting_knowledge` (CKG_SAVE) estaban
declaradas por el grafo y no tenían handler: el motor no podía entrar a DESIGN.

R1: no importa otras features. Usa los runners del composition root
(`memory_tool_runner`) y `build_sprint_service`, con import perezoso para no
crear ciclos.
"""
from __future__ import annotations

import logging
from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult

logger = logging.getLogger(__name__)


def _memory_runner(name: str):
    from fast_mcp.composition import memory_tool_runner

    return memory_tool_runner(name)


def _chain_dir(ctx: ExecutionContext, data: dict[str, Any]) -> str:
    return str(getattr(ctx, "chain_dir", "") or data.get("chain_dir") or "")


def _task_id(ctx: ExecutionContext, data: dict[str, Any]) -> str:
    return str(getattr(ctx, "task_id", "") or data.get("task_id") or "")


def _session_id(ctx: ExecutionContext, data: dict[str, Any]) -> str:
    return str(getattr(ctx, "session_id", "") or data.get("session_id") or "")


def _spring_id(ctx: ExecutionContext, data: dict[str, Any], svc: Any) -> str:
    session_id = _session_id(ctx, data)
    try:
        if session_id and hasattr(svc, "get_session"):
            rec = svc.get_session(session_id) or {}
            sid = str(rec.get("spring_id") or "")
            if sid:
                return sid
        active = svc.get_active_spring()
        return str((active or {}).get("spring_id") or (active or {}).get("_id") or "")
    except Exception:  # noqa: BLE001 — sin sprint no hay candidatos delta
        return ""


def reuse_candidates(ctx: ExecutionContext, data: dict[str, Any]) -> dict[str, Any]:
    """Candidatos de reuse desde CKG, Delta y CPG (F1a, advisory).

    R1: tasks no importa otras features. CKG/CPG se leen por el lector de
    memorias (`agent_context`); Delta por `build_sprint_service` del composition
    root. Cada fuente es fail-open y su fallo se declara en `unmapped`.
    """
    chain_dir = _chain_dir(ctx, data)
    task_id = _task_id(ctx, data)
    query = str(data.get("query") or task_id or "")
    out: dict[str, Any] = {"ckg": [], "delta": [], "cpg": [], "unmapped": []}

    # CKG + CPG vía el lector consolidado de memorias.
    try:
        res = _memory_runner("agent_context")(
            chain_dir=chain_dir, _payload={"task_id": task_id, "text": query}
        )
        if isinstance(res, dict) and not res.get("error"):
            ckg = res.get("ckg") or {}
            for d in (ckg.get("decisions") or [])[:10]:
                if isinstance(d, dict):
                    out["ckg"].append({
                        "source": "ckg",
                        "ref": str(d.get("id") or d.get("_id") or ""),
                        "reason": str(d.get("statement") or d.get("label") or "")[:100],
                    })
            cpg = res.get("cpg") or {}
            for s in (cpg.get("symbols") or [])[:10]:
                name = s.get("name") if isinstance(s, dict) else s
                out["cpg"].append({
                    "source": "cpg",
                    "ref": str(name or ""),
                    "reason": "símbolo/archivo similar en el CPG",
                })
        else:
            out["unmapped"].append({"source": "ckg/cpg", "reason": "agent_context sin respuesta"})
    except Exception as exc:  # noqa: BLE001
        out["unmapped"].append({"source": "ckg/cpg", "reason": f"{type(exc).__name__}: {exc}"})

    # Delta vía sprint service (composition root).
    try:
        from fast_mcp.composition import build_sprint_service

        svc = build_sprint_service(chain_dir)
        if svc is not None:
            spring = _spring_id(ctx, data, svc)
            for spec in (svc.get_specs(spring) or [])[:10]:
                sid = str(spec.get("spec_id") or "")
                if not sid or sid == task_id:
                    continue
                out["delta"].append({
                    "source": "delta",
                    "ref": sid,
                    "reason": str(spec.get("objective") or "")[:100],
                })
        else:
            out["unmapped"].append({"source": "delta", "reason": "sin sprint service"})
    except Exception as exc:  # noqa: BLE001
        out["unmapped"].append({"source": "delta", "reason": f"{type(exc).__name__}: {exc}"})

    return out


def gather_context(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Recolecta code/knowledge/spring context para DESIGN_OBSERVE.

    Cada fuente es fail-open: si falla devuelve ``{}`` y lo deja en ``warnings``.
    El motor nunca se bloquea por contexto faltante; el agente ve qué faltó.
    """
    chain_dir = _chain_dir(ctx, data)
    task_id = _task_id(ctx, data)
    session_id = _session_id(ctx, data)
    warnings: list[str] = []

    knowledge_context: dict[str, Any] = {}
    code_context: dict[str, Any] = {}
    try:
        result = _memory_runner("agent_context")(
            chain_dir=chain_dir,
            _payload={"task_id": task_id, "text": task_id or "design"},
        )
        if isinstance(result, dict) and not result.get("error"):
            knowledge_context = result.get("ckg") or {}
            code_context = result.get("cpg") or {}
        else:
            warnings.append(f"agent_context: {(result or {}).get('error', 'sin respuesta')}")
    except Exception as exc:  # noqa: BLE001 — fail-open
        warnings.append(f"agent_context: {type(exc).__name__}: {exc}")

    spring_context: dict[str, Any] = {}
    try:
        from fast_mcp.composition import sprint_context_for

        # Resolver único (ADR-WALKER-085): si la sesión tiene sprint, ese;
        # si no, el activo; nunca el basename del chain_dir.
        sctx = sprint_context_for(chain_dir, session_id=session_id)
        if sctx.found:
            spring_context = {
                "spring_id": sctx.spring_id,
                "goal": sctx.goal,
                "status": sctx.status,
                "specs": len(sctx.specs),
                "resolved_by": sctx.resolved_by,
            }
            if sctx.diagnostics:
                warnings.extend(f"sprint: {d}" for d in sctx.diagnostics)
    except Exception as exc:  # noqa: BLE001 — fail-open
        warnings.append(f"spring_context: {type(exc).__name__}: {exc}")

    return TaskResult.success(
        data={
            "code_context": code_context,
            "knowledge_context": knowledge_context,
            "spring_context": spring_context,
            "warnings": warnings,
        }
    )


def documenting_knowledge(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Persiste el hecho de diseño en el CKG (acción de DESIGN.CKG_SAVE)."""
    chain_dir = _chain_dir(ctx, data)
    task_id = _task_id(ctx, data)
    session_id = _session_id(ctx, data)
    statement = str(
        data.get("statement") or data.get("fact") or data.get("summary") or ""
    ).strip()
    if not statement:
        statement = f"Design context recorded for {task_id or 'the current task'}"
    node_type = str(data.get("type") or "design")

    try:
        result = _memory_runner("documenting_knowledge")(
            chain_dir=chain_dir,
            statement=statement,
            type=node_type,
            session_id=session_id,
            task_id=task_id,
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult.failure(f"documenting_knowledge: {type(exc).__name__}: {exc}")

    if isinstance(result, dict) and (result.get("ok") is False or result.get("error")):
        return TaskResult.failure(
            f"documenting_knowledge: {result.get('error') or 'falló la escritura'}"
        )

    node_id = ""
    if isinstance(result, dict):
        node_id = str(
            result.get("node_id") or result.get("node_ids") or result.get("id") or ""
        )
    return TaskResult.success(
        data={"ok": True, "node_id": node_id, "statement": statement, "type": node_type}
    )


def register(registry) -> None:
    registry.register(
        "gather_context",
        gather_context,
        metadata={
            "description": "Collect code/knowledge/sprint context for design",
            "tags": ["design"],
            "scope": "internal",
        },
    )
    registry.register(
        "documenting_knowledge",
        documenting_knowledge,
        metadata={
            "description": "Persist the design fact in the CKG",
            "tags": ["design", "ckg"],
            "scope": "internal",
        },
    )
