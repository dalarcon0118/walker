"""context/format.py — Helpers de formateo de contexto (compartidos).

Estos helpers SON del engine (mecanismo puro): formatean piezas de
contexto a texto. Las features de negocio los importan para formatear
sus capas sin duplicar lógica.

El engine NO conoce las capas de negocio; solo ofrece el mecanismo
de formateo. Quién formatea QUÉ lo decide cada feature.
"""
from __future__ import annotations

import json


def fmt_tokens(content: str) -> int:
    """Estimación de tokens (~4 chars/token)."""
    return len(content) // 4


def format_ckg_results(result: dict, max_nodes: int = 20) -> str:
    """Formatea resultados de CKG como texto estructurado."""
    results = result.get("results", [])
    if not results:
        return "(no matching CKG nodes)"

    lines = []
    for r in results[:max_nodes]:
        ntype = r.get("type", "?")
        label = r.get("label", r.get("id", "?"))
        snippet = r.get("snippet", "")
        lines.append(f"  [{ntype}] {label}")
        if snippet:
            s = snippet[:120] + "..." if len(snippet) > 120 else snippet
            lines.append(f"    {s}")
    if len(results) > max_nodes:
        lines.append(f"  ... (+{len(results) - max_nodes} more)")
    return "\n".join(lines)


def format_cpg_results(result: dict, max_results: int = 30) -> str:
    """Formatea resultados de CPG como texto estructurado."""
    data = result if "matches" in result else result.get("data", {})
    matches = data.get("matches", [])
    if not matches:
        return "(no matching CPG nodes)"

    lines = []
    for m in matches[:max_results]:
        ntype = m.get("type", "?")
        name = m.get("name", "?")
        file_path = m.get("file", "")
        line = m.get("line", 0)
        lines.append(f"  {ntype} {name} ({file_path}:{line})")
        rels = m.get("relations", {})
        for rel_type, targets in rels.items():
            preview = ", ".join(targets[:3])
            lines.append(f"    {rel_type}: {preview}")
        cyclo = m.get("cyclo")
        if cyclo:
            lines.append(f"    cyclomatic: {cyclo}")
    if len(matches) > max_results:
        lines.append(f"  ... (+{len(matches) - max_results} more)")
    return "\n".join(lines)


def format_trace_results(result: dict, max_events: int = 8) -> str:
    """Formatea resultados de Trace como texto cronológico."""
    data = result if "results" in result else result.get("data", {})
    all_results = []
    for items in data.get("results", {}).values():
        all_results.extend(items)
    if not all_results:
        return "(no trace events)"

    all_results.sort(
        key=lambda e: e.get("timestamp", e.get("at", "")),
        reverse=True,
    )
    lines = []
    for evt in all_results[:max_events]:
        ts = evt.get("timestamp", evt.get("at", ""))
        etype = evt.get("event_type", evt.get("type", "event"))
        data_str = json.dumps(evt.get("input", evt.get("message", "")))
        if len(data_str) > 150:
            data_str = data_str[:150] + "..."
        lines.append(f"  [{ts}] {etype}: {data_str}")
    if len(all_results) > max_events:
        lines.append(f"  ... (+{len(all_results) - max_events} more)")
    return "\n".join(lines)


def format_state_result(result: dict) -> str:
    """Formatea resultado de State como YAML-like."""
    lines = []
    if "node" in result:
        lines.append(f"node: {result.get('node', '?')}")
    if "task_id" in result:
        lines.append(f"task_id: {result.get('task_id', '')}")
    if "sprint_id" in result:
        lines.append(f"sprint_id: {result.get('sprint_id', '')}")
    if "sprint_goal" in result and result.get("sprint_goal"):
        lines.append(f"sprint_goal: {result.get('sprint_goal', '')}")
    if "sprint_status" in result and result.get("sprint_status"):
        lines.append(f"sprint_status: {result.get('sprint_status', '')}")
    if "match" in result:
        lines.append(f"match: {result.get('match', False)}")
    history = result.get("history", [])
    if history:
        lines.append(f"history: {len(history)} events")
    return "\n".join(lines)


def format_delta_results(result: dict) -> str:
    """Formatea resultados de DeltaGraph como texto."""
    data = result if "results" in result else result.get("data", {})
    all_results = []
    for items in data.get("results", {}).values():
        all_results.extend(items)
    if not all_results:
        return "(no delta results)"

    lines = []
    for r in all_results[:15]:
        task_id = r.get("task_id", r.get("id", "?"))
        objective = r.get("objective", r.get("description", ""))[:100]
        status = r.get("status", "?")
        lines.append(f"  {task_id} [{status}] {objective}")
    if len(all_results) > 15:
        lines.append(f"  ... (+{len(all_results) - 15} more)")
    return "\n".join(lines)
