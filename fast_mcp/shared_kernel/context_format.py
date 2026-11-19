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
