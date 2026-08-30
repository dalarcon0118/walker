"""app/context_format.py — Formatters de resultados de las memorias del walker.

NEGOCIO DEL WALKER (no capa común genérica, ADR-WALKER-067 §7.5/L6): los
formatters de CKG/CPG/Delta/Trace saben el shape de cada memoria del walker.
Viven en el HOST (app), no en `shared/common/context` (que queda con solo
helpers genéricos, p.ej. `fmt_tokens`). Cada feature importa SU formatter.
"""
from __future__ import annotations

import json


def format_ckg_results(result: dict, max_nodes: int = 20) -> str:
    """Formatea resultados de CKG como texto estructurado."""
    results = result.get("results", [])
    if not results:
        return "(no matching CKG nodes)"

    lines = []
    for r in results[:max_nodes]:
        ntype = r.get("type", "?")
        scope = r.get("scope", "")
        statement = r.get("statement") or r.get("subject") or r.get("description", "")
        label = r.get("label") or statement[:70] or r.get("id", "?")
        severity = r.get("severity", "")
        head = f"  [{ntype}] {label}"
        if scope:
            head += f" (scope: {scope})"
        if severity:
            head += f" [{severity}]"
        lines.append(head)
        if statement and statement != label:
            s = statement[:120] + "..." if len(statement) > 120 else statement
            lines.append(f"    {s}")
    if len(results) > max_nodes:
        lines.append(f"  ... (+{len(results) - max_nodes} more)")
    return "\n".join(lines)


def format_cpg_results(result: dict, max_results: int = 30) -> str:
    """Formatea resultados de CPG como texto estructurado."""
    results = result.get("results", [])
    if not results:
        return "(no matching CPG nodes)"

    lines = []
    for m in results[:max_results]:
        ntype = m.get("type", "?")
        name = m.get("name", "?")
        file_path = m.get("file", "")
        line = m.get("line", 0)
        lines.append(f"  {ntype} {name} ({file_path}:{line})")
        sig = m.get("signature", "")
        if sig:
            lines.append(f"    {sig[:90]}")
        rels = m.get("relations", {})
        for rel_type, targets in rels.items():
            preview = ", ".join(targets[:3])
            lines.append(f"    {rel_type}: {preview}")
        cyclo = m.get("cyclo")
        if cyclo:
            lines.append(f"    cyclomatic: {cyclo}")
    if len(results) > max_results:
        lines.append(f"  ... (+{len(results) - max_results} more)")
    return "\n".join(lines)


def format_trace_results(result: dict, max_events: int = 8) -> str:
    """Formatea resultados de Trace como texto cronológico."""
    raw = result.get("results")
    if raw is None:
        raw = result.get("data", {}).get("results", {})
    if isinstance(raw, dict):
        all_results = []
        for items in raw.values():
            all_results.extend(items)
    else:
        all_results = list(raw or [])
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


def format_delta_results(result: dict) -> str:
    """Formatea resultados de DeltaGraph como texto."""
    raw = result.get("results")
    if raw is None:
        raw = result.get("data", {}).get("results", {})
    if isinstance(raw, dict):
        all_results = []
        for items in raw.values():
            all_results.extend(items)
    else:
        all_results = list(raw or [])
    if not all_results:
        return "(no delta results)"

    lines = []
    for r in all_results[:15]:
        task_id = r.get("spec_id", r.get("task_id", r.get("id", "?")))
        objective = r.get("objective", r.get("description", ""))[:100]
        status = r.get("status", "?")
        lines.append(f"  {task_id} [{status}] {objective}")
    if len(all_results) > 15:
        lines.append(f"  ... (+{len(all_results) - 15} more)")
    return "\n".join(lines)