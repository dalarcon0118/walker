#!/usr/bin/env python3
"""Mide el nivel de metacognición del agente por sesión.

Componentes (0-100):
  - Reconocimiento: lecturas de brief con gaps (0-20)
  - Memoria: uso de search_memories / inspect_codebase (0-20)
  - Estrategia: uso de plan_diff / session_plan / decisions_view (0-20)
  - Verificación: run_tests / verify_predicates (0-20)
  - Remedio: mirror_acted_on (0-20)

Uso: python3 scripts/metacognition_score.py [session_id] [--chain-dir PATH]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def main():
    session_id = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else ""
    chain_dir = str(ROOT)
    if "--chain-dir" in sys.argv:
        idx = sys.argv.index("--chain-dir")
        chain_dir = sys.argv[idx + 1]

    from fast_mcp.features.event_tracing.application.services.trace_factory import build_trace_service
    svc = build_trace_service(chain_dir)
    if svc is None:
        print("ERROR: no TraceService")
        sys.exit(1)
    events = svc.load_traces(chain_dir)
    if session_id:
        events = [e for e in events if str((getattr(e, "metadata", None) or {}).get("session_id", "")) == session_id]

    events.sort(key=lambda e: str(getattr(e, "timestamp", "") or ""))

    # Contadores
    brief_reads = 0
    brief_with_gaps = 0
    mirror_acted = 0
    memory_calls = 0
    strategy_calls = 0
    verify_calls = 0
    close_blocked = 0
    orphan_specs = 0
    workflow_ignored = 0

    # Reusar mirror_acted_on logic simplificada
    brief_reads_with_gap_idx = []
    for i, e in enumerate(events):
        et = str(getattr(e, "event_type", "") or "")
        data = getattr(e, "data", None) or {}
        if not isinstance(data, dict):
            data = {}
        tool = str(data.get("tool_name") or data.get("tool") or "").lower()
        if et == "brief_injected":
            brief_reads += 1
            gaps = (data.get("self") or {}).get("gaps") or data.get("gaps") or []
            if gaps:
                brief_with_gaps += 1
                brief_reads_with_gap_idx.append(i)
        if et == "close_blocked":
            close_blocked += 1
        if et == "workflow_suggestion_ignored":
            workflow_ignored += 1
        if et == "session_orphan_specs":
            orphan_specs += len(data.get("specs") or [])
        # Tool calls (ToolInvoked sin session_id, se cuentan globalmente)
        if tool:
            if tool in ("search_memories", "inspect_codebase", "code_context", "agent_context"):
                memory_calls += 1
            if tool in ("plan_diff", "session_plan", "decisions_view", "cockpit", "what_changed", "session_summary"):
                strategy_calls += 1
            if tool in ("run_tests", "verify_predicates"):
                verify_calls += 1

    # mirror_acted: verificar si después de un brief con gap hay un remedio
    REMEDY = {"run_tests", "verify_predicates", "session_plan", "implement_task", "capture"}
    for idx in brief_reads_with_gap_idx:
        next_tools = set()
        count = 0
        for j in range(idx + 1, len(events)):
            d = getattr(events[j], "data", None) or {}
            if not isinstance(d, dict):
                d = {}
            t = str(d.get("tool_name") or d.get("tool") or "").lower()
            if t:
                next_tools.add(t.split("/")[-1])
                count += 1
            if count >= 5:
                break
        if REMEDY & next_tools:
            mirror_acted += 1

    # Score
    score = 0
    # Reconocimiento (0-20): brief reads con gaps
    score += min(20, brief_with_gaps * 5)
    # Memoria (0-20): uso de search/inspect
    score += min(20, memory_calls * 2)
    # Estrategia (0-20): plan_diff, session_plan, decisions_view, cockpit
    score += min(20, strategy_calls * 2)
    # Verificación (0-20): run_tests, verify_predicates
    score += min(20, verify_calls * 5)
    # Remedio (0-20): mirror_acted_on ratio
    if brief_with_gaps > 0:
        score += int(20 * (mirror_acted / brief_with_gaps))
    # Penalizaciones
    score -= min(20, close_blocked * 2)
    score -= min(20, orphan_specs * 2)
    score -= min(10, workflow_ignored)
    score = max(0, min(100, score))

    print(f"Sesión: {session_id or '(todas)'}")
    print(f"  brief reads: {brief_reads} (con gap: {brief_with_gaps})")
    print(f"  mirror_acted: {mirror_acted}/{brief_with_gaps}")
    print(f"  memory_calls: {memory_calls}")
    print(f"  strategy_calls: {strategy_calls}")
    print(f"  verify_calls: {verify_calls}")
    print(f"  close_blocked: {close_blocked}")
    print(f"  orphan_specs: {orphan_specs}")
    print(f"  workflow_ignored: {workflow_ignored}")
    print(f"  METACOGNITION SCORE: {score}/100")


if __name__ == "__main__":
    main()
