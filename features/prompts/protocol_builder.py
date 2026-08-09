"""protocol_builder.py — Genera el protocolo MCP dinámicamente.

Mezcla:
  1. Texto estático del template protocol/mcp.md (secciones narrativas).
  2. Secciones vivas generadas desde código:
       - Tools públicas disponibles (list_tools)
       - Mapa del workflow (workflow_map)
       - Mecanismo anti-pérdida / uncaptured_work
"""
from __future__ import annotations

from typing import Any

from features.engine.application.tool_catalog import list_tools
from features.engine.application.workflow_map import compose_workflow_map
from features.engine.application.uncaptured_work import UncapturedWorkResult


def _load_static_template() -> str:
    from features.prompts.loader import load_static
    return load_static("protocol/mcp")


def _build_tools_section() -> str:
    tools = list_tools()
    if not tools:
        return "No tools available."
    lines = ["Available tools:"]
    for t in tools:
        name = t.get("name", "")
        desc = t.get("description", "")
        lines.append(f"  - {name}: {desc}")
    return "\n".join(lines)


def _build_workflow_section() -> str:
    try:
        wf_map = compose_workflow_map()
    except Exception as exc:
        return f"Workflow map unavailable ({type(exc).__name__}: {exc})."
    analysis = wf_map.get("analysis", {})
    off_sprint = analysis.get("off_sprint", {})
    mechanism = off_sprint.get("mechanism", {})
    lines = [
        "Workflow map:",
        f"  nodes: {analysis.get('node_count', '?')}",
        f"  edges: {analysis.get('edge_count', '?')}",
        f"  cycles: {analysis.get('cycles_count', '?')}",
        f"  terminals: {', '.join(analysis.get('terminal_nodes', [])) or '?'}",
        "",
        "Off-sprint task creation:",
        f"  can_create_formal_outside_sprint: {off_sprint.get('can_create_formal_outside_sprint')}",
        f"  mechanism: {mechanism.get('task')} — {mechanism.get('description', '')}",
        f"  usage: {mechanism.get('usage', '')}",
    ]
    return "\n".join(lines)


def _build_capture_task_section() -> str:
    try:
        wf_map = compose_workflow_map()
        analysis = wf_map.get("analysis", {})
        off_sprint = analysis.get("off_sprint", {})
        mechanism = off_sprint.get("mechanism", {})
    except Exception:
        mechanism = {}
    lines = [
        "6. SESSION WORK LOG (MANDATORY): when you COMPLETE a piece of work",
        "   (implemented a feature, fixed a bug, refactored, indexed, etc.),",
        "   PERSIST it with capture_task so it is not lost:",
        f"     - {mechanism.get('usage', 'walker <project>/capture_task ...')}",
        "   This stores the task under an INFORMAL SPRING by date",
        "   (session-YYYYMMDD) that the DeltaGraph indexes as historical",
        "   context. The agent's work never gets lost, even without a formal",
        "   spring. The formal way remains plan_sprint + the workflow.",
        "   DECISIONS: if the work involved an architecture decision, INCLUDE it",
        "   in the same capture_task payload:",
        f"     - {mechanism.get('usage', 'walker <project>/capture_task ...').replace('objective', 'decisions[0].statement')}",
        "   capture_task registers it in the CKG automatically (with lifecycle).",
        "   AUTO-DETECTION: if you do NOT report decisions but the 'files' touch",
        "   architecture layers (contracts, domain, workflow, config, engine,",
        "   repositories, providers), capture_task detects it and generates the",
        "   decision automatically using your objective as context.",
    ]
    return "\n".join(lines)


def build_mcp_protocol() -> str:
    static = _load_static_template()
    lines = static.splitlines()

    try:
        tools_section = _build_tools_section()
        workflow_section = _build_workflow_section()
        capture_section = _build_capture_task_section()
    except Exception as exc:
        return static

    # Insertar sección 2 dinámica después de "2. TO ADVANCE..."
    # Insertar workflow map después de la sección 5 completa.
    result: list[str] = []
    i = 0
    inserted_wf = False
    while i < len(lines):
        line = lines[i]
        result.append(line)
        if line.strip().startswith("2. TO ADVANCE in the workflow"):
            result.append("")
            result.extend(tools_section.splitlines())
            result.append("")
        if not inserted_wf and line.strip().startswith("5. REUSE what exists"):
            # Avanzar hasta el final del párrafo de la sección 5
            j = i + 1
            while j < len(lines) and lines[j].strip() and not lines[j].strip().startswith("6."):
                result.append(lines[j])
                j += 1
            i = j - 1
            result.append("")
            result.extend(workflow_section.splitlines())
            result.append("")
            inserted_wf = True
        i += 1

    result_text = "\n".join(result)

    # Reemplazar sección 6 completa
    start = result_text.find("6. SESSION WORK LOG")
    if start != -1:
        before = result_text[:start]
        after = result_text[start:]
        end = after.find("\n\n")
        if end == -1:
            end = len(after)
        return before.rstrip() + "\n\n" + capture_section + "\n" + after[end:].lstrip()

    return result_text
