"""metacognition.py — una sola fórmula de score (ADR-WALKER-084).

El espejo (`self`) lo proyecta `knowledge._session_self` desde el TraceGraph.
Este módulo no reimplementa esa proyección: recibe el `self_state` y la
telemetría por tool (`data/metrics/tool_calls.jsonl`, que ya lleva
`session_id`/`task_id`) y calcula el score que consumen `brief` y
`scripts/metacognition_score.py`.

Regla: sin datos no hay score. `None` es distinto de `0` (0 es "hay datos y el
resultado fue malo"; `None` es "no hay sensor").
"""
from __future__ import annotations

from typing import Any, Iterable

from fast_mcp.shared_kernel.telemetry.tool_metrics import metrics_path, read_records

MEMORY_TOOLS = frozenset(
    {
        "search_memories",
        "recall_knowledge",
        "knowledge_query",
        "code_context",
        "code_search",
        "inspect_codebase",
        "read_code",
        "agent_context",
        "preflight",
    }
)
STRATEGY_TOOLS = frozenset(
    {
        "session_plan",
        "plan_diff",
        "decisions_view",
        "cockpit",
        "what_changed",
        "session_summary",
        "brief",
    }
)
VERIFY_TOOLS = frozenset({"run_tests", "verify_predicates"})

#: Remedios por código de gap (mismo contrato que scripts/mirror_acted_on.py).
REMEDY_MAP: dict[str, set[str]] = {
    "no_verify": {"run_tests", "verify_predicates"},
    "no_evidence": {"run_tests", "verify_predicates"},
    "close_blocked": {"run_tests", "verify_predicates"},
    "denials": {"session_bind", "session_plan", "implement_task"},
    "scope_expanded": {"session_plan"},
    "unverified_criteria": {"implement_task", "verify_predicates"},
}


def load_tool_records(path: Any = None) -> list[dict[str, Any]]:
    """Lee la telemetría por tool (JSONL). Un fallo devuelve [] y lo dice."""
    try:
        return read_records(path or metrics_path())
    except Exception:  # noqa: BLE001 — la observabilidad no tumba el lector
        return []


def select_records(
    records: Iterable[dict[str, Any]],
    session_id: str = "",
    task_id: str = "",
) -> list[dict[str, Any]]:
    """Filtra por sesión; si el registro no tiene sesión, cae a task_id."""
    selected: list[dict[str, Any]] = []
    for record in records:
        sid = str(record.get("session_id") or "")
        tid = str(record.get("task_id") or "")
        if not session_id:
            selected.append(record)
            continue
        if sid == session_id:
            selected.append(record)
            continue
        # Fallback explícito: una llamada con task_id pero sin session_id
        # pertenece a la tarea; no se descarta en silencio.
        if not sid and task_id and tid == task_id:
            selected.append(record)
    return selected


def tool_counts(
    records: Iterable[dict[str, Any]],
    session_id: str = "",
    task_id: str = "",
) -> dict[str, int]:
    """Cuenta las llamadas de la sesión por familia (sin doble conteo)."""
    selected = select_records(records, session_id, task_id)
    counts = {
        "memory": 0,
        "strategy": 0,
        "verify": 0,
        "brief": 0,
        "total": len(selected),
        "unattributed": 0,
    }
    for record in selected:
        tool = str(record.get("tool") or "")
        if not str(record.get("session_id") or "") and not str(record.get("task_id") or ""):
            counts["unattributed"] += 1
        if tool in MEMORY_TOOLS:
            counts["memory"] += 1
        if tool in STRATEGY_TOOLS:
            counts["strategy"] += 1
        if tool in VERIFY_TOOLS:
            counts["verify"] += 1
        if tool == "brief":
            counts["brief"] += 1
    return counts


def mirror_acted_on(
    events: Iterable[Any],
    records: Iterable[dict[str, Any]],
    session_id: str = "",
) -> dict[str, Any]:
    """¿La lectura del espejo cambió la acción siguiente?

    Una lectura "con gap" es `brief_injected` con gaps. Se considera actuada si
    en las próximas 5 llamadas de tool de la sesión aparece el remedio de algún
    gap. Sin lecturas con gap se devuelve `rate=None` (sin datos), no 0.
    """
    reads: list[tuple[str, set[str]]] = []
    for event in events:
        if str(getattr(event, "event_type", "") or "") != "brief_injected":
            continue
        data = getattr(event, "data", None) or {}
        if not isinstance(data, dict):
            data = {}
        gaps = (data.get("self") or {}).get("gaps") or data.get("gaps") or []
        codes = {
            str((gap.get("code") if isinstance(gap, dict) else gap) or "")
            for gap in gaps
        }
        codes.discard("")
        if codes:
            reads.append((str(getattr(event, "timestamp", "") or ""), codes))

    selected = select_records(records, session_id)
    brief_calls = sum(
        1 for record in selected if str(record.get("tool") or "") == "brief"
    )
    ordered = sorted(
        (str(record.get("ts") or ""), str(record.get("tool") or ""))
        for record in selected
        if record.get("tool")
    )
    acted = 0
    for timestamp, codes in reads:
        expected: set[str] = set()
        for code in codes:
            expected |= REMEDY_MAP.get(code, set())
        next_tools = [tool for ts, tool in ordered if not timestamp or ts > timestamp][:5]
        if expected & set(next_tools):
            acted += 1
    return {
        "brief_reads_with_gaps": len(reads),
        "brief_calls": brief_calls,
        "acted": acted,
        "rate": (acted / len(reads)) if reads else None,
    }


def compute_score(
    self_state: dict[str, Any] | None,
    counts: dict[str, int],
    *,
    mirror_acted: int | None = None,
    brief_with_gaps: int | None = None,
) -> dict[str, Any]:
    """Fórmula única (la que ya usaba scripts/metacognition_score.py).

    Componentes (0-100):
      - Reconocimiento: lecturas de brief con gaps (0-20)
      - Memoria: tool calls de memoria (0-20)
      - Estrategia: tool calls de estrategia (0-20)
      - Verificación: run_tests/verify_predicates (0-20)
      - Remedio: mirror_acted / lecturas con gap (0-20)
      - Penalizaciones: close_blocked, orphan_specs, workflow_ignored.
    """
    state = self_state or {}
    gaps = state.get("gaps") or []
    verified = state.get("verified")
    denials = int((state.get("denials") or {}).get("total") or 0)
    close_blocked = int(state.get("close_blocked") or 0)
    orphan_specs = int(state.get("orphan_specs") or 0)
    workflow_ignored = int(state.get("workflow_ignored") or 0)
    reads_with_gaps = (
        int(brief_with_gaps)
        if brief_with_gaps is not None
        else int(state.get("brief_with_gaps") or 0)
    )
    memory = int(counts.get("memory") or 0)
    strategy = int(counts.get("strategy") or 0)
    verify = int(counts.get("verify") or 0)
    brief_calls = int(counts.get("brief") or 0)
    injected_reads = int(state.get("brief_reads") or 0)
    brief_reads = max(injected_reads, brief_calls)
    acted = int(mirror_acted if mirror_acted is not None else (state.get("mirror_acted") or 0))

    has_data = bool(
        state.get("writes")
        or verified is not None
        or reads_with_gaps
        or memory
        or strategy
        or verify
        or denials
        or close_blocked
    )
    components = {
        "verified": verified is True,
        "gaps": len(gaps),
        "denials": denials,
        "brief_reads": brief_reads,
        "brief_calls": brief_calls,
        "brief_with_gaps": reads_with_gaps,
        "memory_calls": memory,
        "strategy_calls": strategy,
        "verify_calls": verify,
        "mirror_acted": acted,
        "close_blocked": close_blocked,
        "orphan_specs": orphan_specs,
        "workflow_ignored": workflow_ignored,
    }
    if not has_data:
        return {
            "score": None,
            "components": components,
            "missing": ["sin datos: la sesión no tiene eventos ni tool calls"],
        }

    score = 0
    score += min(20, reads_with_gaps * 5)
    score += min(20, memory * 2)
    score += min(20, strategy * 2)
    score += min(20, verify * 5)
    if reads_with_gaps:
        score += int(20 * min(1.0, acted / reads_with_gaps))
    score -= min(20, close_blocked * 2)
    score -= min(20, orphan_specs * 2)
    score -= min(10, workflow_ignored)
    return {
        "score": max(0, min(100, score)),
        "components": components,
        "missing": [],
    }


def compute_metacognition(
    self_state: dict[str, Any] | None,
    *,
    session_id: str = "",
    task_id: str = "",
    records: list[dict[str, Any]] | None = None,
    impact: dict[str, Any] | None = None,
    plan: dict[str, Any] | None = None,
    review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score + conocimiento + regulación, desde el mismo `self_state`.

    `brief` y `scripts/metacognition_score.py` llaman acá: no hay una segunda
    fórmula. Las tool calls salen de la telemetría con `session_id`.
    """
    state = self_state or {}
    counts = tool_counts(
        records if records is not None else load_tool_records(),
        session_id=session_id,
        task_id=task_id,
    )
    score = compute_score(
        state,
        counts,
        mirror_acted=(state.get("mirror_acted") if state.get("mirror_acted") is not None else None),
        brief_with_gaps=state.get("brief_with_gaps"),
    )

    gaps = state.get("gaps") or []
    verified = state.get("verified")
    strategies: list[str] = []
    if gaps:
        strategies.append("remediar gaps")
    if state.get("writes") and verified is not True:
        strategies.append("verificar")
    if counts.get("memory"):
        strategies.append("consultar memoria")
    if review is not None:
        strategies.append("revisar CPG")
    if not strategies:
        strategies.append("pedir brief")

    first_remedy = ""
    if gaps and isinstance(gaps[0], dict):
        first_remedy = str(gaps[0].get("remedy") or "")
    if not first_remedy and state.get("writes") and verified is not True:
        first_remedy = "correr run_tests y verificar"

    result = dict(score)
    result["knowledge"] = {
        "self": {
            "verified": verified,
            "gaps": len(gaps),
            "denials": int((state.get("denials") or {}).get("total") or 0),
        },
        "task": {
            "callers": (impact or {}).get("callers", 0),
            "risk": (impact or {}).get("risk", ""),
            "criteria": len((plan or {}).get("acceptance") or []),
        },
        "strategies": strategies,
    }
    result["regulation"] = {
        "plan": {
            "strategy": strategies[0],
            "why": "gaps/impacto/decisiones del ancla",
        },
        "monitor": {
            "gaps": len(gaps),
            "next": first_remedy or "sin gaps accionables",
        },
        "evaluate": {
            "tests": verified,
            "review": (review or {}).get("passed"),
            "adjust": "definir predicados" if (plan or {}).get("unverified") else "",
        },
    }
    result["counts"] = counts
    return result
