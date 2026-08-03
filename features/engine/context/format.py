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
    """Formatea resultados de CKG como texto estructurado.

    Contract: recibe el resultado normalizado de QueryManager.query_layer
    → {"ok", "layer", "total", "results": [<source>...]}. Cada source es el
    documento CKG real (type, scope, statement/subject, severity, tags).
    """
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
    """Formatea resultados de CPG como texto estructurado.

    Contract: resultado normalizado → {"ok", "results": [<source>...]}.
    Cada source: name, type, file, line, signature, kind.
    """
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
    """Formatea resultados de Trace como texto cronológico.

    Contract: resultado normalizado → {"ok", "results": [<source>...]}.
    Compat backward: acepta el formato legacy {"results": {layer: [items]}}.
    """
    raw = result.get("results")
    if raw is None:
        # Legacy: results era dict de listas por capa
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

    # ── Resultado del paso anterior (QUÉ SUCEDIÓ) ──────────────────
    # last_fields: campos del submit previo (Q&A del ANALYZE, decisiones,
    # resultados de IMPLEMENT, etc.). Permite que el user gate presente
    # al PO qué se hizo antes de pedir la decisión.
    last_fields = result.get("last_fields", {})
    if last_fields:
        lines.append("last_step_fields:")
        for k, v in last_fields.items():
            if isinstance(v, str) and len(v) > 160:
                v = v[:160] + "..."
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def format_delta_results(result: dict) -> str:
    """Formatea resultados de DeltaGraph como texto.

    Contract: resultado normalizado → {"ok", "results": [<source>...]}.
    Compat backward: acepta el formato legacy {"results": {layer: [items]}}.
    """
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
        task_id = r.get("task_id", r.get("id", "?"))
        objective = r.get("objective", r.get("description", ""))[:100]
        status = r.get("status", "?")
        lines.append(f"  {task_id} [{status}] {objective}")
    if len(all_results) > 15:
        lines.append(f"  ... (+{len(all_results) - 15} more)")
    return "\n".join(lines)
