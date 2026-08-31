"""test_context_budget_bdd.py — Mide el costo real de contexto del catálogo de tools.

Estima tokens (~4 chars/token) del tools/list completo de walker (31 tools del
legacy, SSOT tool_catalog) vs el subset actual de fast_mcp (3 tools). Sirve de
insumo para la decisión de carga selectiva de tools (patrón Task Master).
"""

from __future__ import annotations

import json
import os
import sys


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "mcp"))

CHARS_PER_TOKEN = 4.0


def _tokens(obj) -> int:
    return int(len(json.dumps(obj, ensure_ascii=False)) / CHARS_PER_TOKEN)


def test_presupuesto_contexto():
    from app.container import AppContainer
    from shared.core.engine.application import tool_catalog

    app = AppContainer.build()
    tools = tool_catalog.list_tools(registry=app.module_registry)

    print(f"\n===== Catálogo real de walker: {len(tools)} tools =====")

    full = _tokens(tools)
    print(f"  FULL (inputSchema+outputSchema+metadata): ~{full:,} tokens")

    sin_output = [
        {k: v for k, v in t.items() if k != "outputSchema"} for t in tools
    ]
    print(f"  sin outputSchema:                         ~{_tokens(sin_output):,} tokens")

    sin_meta = [
        {k: v for k, v in t.items() if k not in ("outputSchema", "metadata")}
        for t in tools
    ]
    print(f"  sin outputSchema ni metadata:             ~{_tokens(sin_meta):,} tokens")

    # Por herramienta (ordenado por costo)
    print("\n  Top 8 tools por costo (JSON completo):")
    per = sorted(((_tokens(t), t["name"]) for t in tools), reverse=True)
    for tok, name in per[:8]:
        print(f"    {name:<28} ~{tok:,}")

    # Promedio
    avg = full / max(len(tools), 1)
    print(f"\n  promedio por tool: ~{int(avg)} tokens")
    print("  referencia Task Master: 36 tools ≈ 21k tokens | 15 ≈ 10k | 7 ≈ 5k")

    # Proyección de escenarios de carga selectiva para walker (31 tools)
    grupos = {
        "core (3)": ["prepare", "list", "status"],
        "workflow (6)": ["prepare", "list", "status", "submit", "transition", "help"],
        "memorias (F2)": [
            "build_code_index", "index_repo", "update_incremental_code",
            "extract_knowledge", "knowledge_query", "knowledge_metadata",
            "knowledge_agent_context", "session_knowledge",
            "code_context", "inspect_codebase", "read_code", "code_search",
        ],
    }
    by_name = {t["name"]: t for t in tools}
    print("\n  Escenarios de carga selectiva (proyección real):")
    for label, names in grupos.items():
        subset = [by_name[n] for n in names if n in by_name]
        print(f"    {label:<14} {len(subset):>2} tools ≈ {_tokens(subset):,} tokens")
    print("===== fin =====")
