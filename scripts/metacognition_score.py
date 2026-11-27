#!/usr/bin/env python3
"""Mide metacognición con la MISMA fórmula que `brief` (ADR-WALKER-084).

El score no es una segunda opinión: `brief` y este script llaman a
`compute_metacognition`/`compute_score` del mismo módulo. La sesión se resuelve
por `session_id`; las tool calls salen de `tool_calls.jsonl` (que lleva
session_id/task_id), no de los `ToolInvoked` legacy del trace.

Uso:
  python3 scripts/metacognition_score.py [session_id] [--chain-dir PATH]
  python3 scripts/metacognition_score.py --all
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _global_state(events) -> dict:
    """Contadores de eventos para el modo global (sin session_id)."""
    state = {
        "brief_reads": 0,
        "brief_with_gaps": 0,
        "orphan_specs": 0,
        "workflow_ignored": 0,
        "close_blocked": 0,
        "denials": {"total": 0, "by_rule": {}},
        "gaps": [],
    }
    for event in events:
        etype = str(getattr(event, "event_type", "") or "")
        data = getattr(event, "data", None) or {}
        if not isinstance(data, dict):
            data = {}
        if etype == "brief_injected":
            state["brief_reads"] += 1
            gaps = (data.get("self") or {}).get("gaps") or data.get("gaps") or []
            if gaps:
                state["brief_with_gaps"] += 1
        elif etype == "close_blocked":
            state["close_blocked"] += 1
        elif etype == "session_orphan_specs":
            state["orphan_specs"] += len(data.get("specs") or [])
        elif etype == "workflow_suggestion_ignored":
            state["workflow_ignored"] += 1
        elif etype == "flight_control_denied":
            state["denials"]["total"] += 1
            gate = str(data.get("gate") or data.get("rule_id") or "sin_gate")
            state["denials"]["by_rule"][gate] = state["denials"]["by_rule"].get(gate, 0) + 1
    return state


def _print(session_id: str, result: dict) -> None:
    comp = result.get("components") or {}
    score = result.get("score")
    print(f"Sesión: {session_id or '(todas)'}")
    print(f"  brief reads: {comp.get('brief_reads', 0)} (con gap: {comp.get('brief_with_gaps', 0)})")
    print(f"  mirror_acted: {comp.get('mirror_acted', 0)}/{comp.get('brief_with_gaps', 0)}")
    print(f"  memory_calls: {comp.get('memory_calls', 0)}")
    print(f"  strategy_calls: {comp.get('strategy_calls', 0)}")
    print(f"  verify_calls: {comp.get('verify_calls', 0)}")
    print(f"  close_blocked: {comp.get('close_blocked', 0)}")
    print(f"  orphan_specs: {comp.get('orphan_specs', 0)}")
    print(f"  workflow_ignored: {comp.get('workflow_ignored', 0)}")
    if score is None:
        print("  METACOGNITION SCORE: sin datos (no es 0: no hay sensor)")
    else:
        print(f"  METACOGNITION SCORE: {score}/100")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    session_id = args[0] if args and args[0] != "--all" else ""
    chain_dir = str(ROOT)
    if "--chain-dir" in sys.argv:
        idx = sys.argv.index("--chain-dir")
        chain_dir = sys.argv[idx + 1]

    from fast_mcp.features.event_tracing.application.services.metacognition import (
        compute_metacognition,
        compute_score,
        load_tool_records,
        mirror_acted_on,
        tool_counts,
    )

    records = load_tool_records()
    if session_id:
        from fast_mcp.memories.context import MemoryContext
        from fast_mcp.memories.handlers.knowledge import _session_self

        ctx = MemoryContext.resolve(chain_dir)
        if ctx is None:
            print(f"ERROR: no se pudo resolver el proyecto en {chain_dir}")
            sys.exit(1)
        self_state, _missing, _degraded = _session_self(ctx, session_id)
        result = compute_metacognition(
            self_state,
            session_id=session_id,
            records=records,
        )
        _print(session_id, result)
        return

    from fast_mcp.features.event_tracing.application.services.trace_factory import (
        build_trace_service,
    )

    service = build_trace_service(chain_dir)
    events = service.load_traces(chain_dir) if service is not None else []
    state = _global_state(events)
    mirror = mirror_acted_on(events, records, "")
    state["mirror_acted"] = int(mirror.get("acted") or 0)
    result = compute_score(
        state,
        tool_counts(records, ""),
        mirror_acted=state["mirror_acted"],
        brief_with_gaps=state["brief_with_gaps"],
    )
    _print("", result)


if __name__ == "__main__":
    main()
