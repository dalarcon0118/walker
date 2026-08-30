"""protocol_builder.py — Genera el protocolo MCP dinámicamente.

Mezcla:
   1. Texto estático del template protocol/mcp.md (secciones narrativas).
   2. Secciones vivas generadas desde código:
        - Tools públicas disponibles (list_tools)
        - Mapa del workflow (workflow_map)
        - Mecanismo anti-pérdida / uncaptured_work
        - Tools de análisis (CPG + phase intelligence)
"""

from __future__ import annotations


from shared.core.engine.application.tool_catalog import list_tools
from shared.core.engine.application.workflow_map import compose_workflow_map


ANALYSIS_TOOL_PREFIXES = ("cpg_", "kg_", "analyze_")
PHASE_TOOL_NAMES = {
    "task_context": "file/task context before editing (IMPLEMENT phase)",
    "diagnose_failure": "failure diagnosis with conventions (REFINE phase)",
    "verify_evidence": "verification coverage (VERIFY phase)",
    "sprint_evidence": "sprint planning evidence (SPRINT.PLANNING phase)",
}


def _load_static_template() -> str:
    from features.workflow.application.prompts.static_loader import load_static

    return load_static("protocol/mcp")


def _build_tools_section() -> str:
    from shared.core.engine.application.tool_catalog import list_tools
    from shared.libs.module_system.registry import module_registry

    registry = module_registry
    tools = list_tools(registry=registry)
    if not tools:
        return "No tools available."
    lines = ["Available tools:"]
    for t in tools:
        name = t.get("name", "")
        desc = t.get("description", "")
        lines.append(f"  - {name}: {desc}")
    return "\n".join(lines)


def _build_workflow_section(wf_map: dict) -> str:
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


def _build_capture_task_section(wf_map: dict) -> str:
    analysis = wf_map.get("analysis", {})
    off_sprint = analysis.get("off_sprint", {})
    mechanism = off_sprint.get("mechanism", {})
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


def _build_analysis_section() -> str:
    from shared.libs.module_system.registry import module_registry

    registry = module_registry
    tools = list_tools(registry=registry)
    if not tools:
        return ""

    lines = ["Available analysis tools:"]
    for t in tools:
        name = t.get("name", "")
        if not name.startswith(ANALYSIS_TOOL_PREFIXES):
            continue
        if name in PHASE_TOOL_NAMES:
            desc = PHASE_TOOL_NAMES[name]
        else:
            desc = t.get("description", "")
        lines.append(f"  - {name}: {desc}")
    return "\n".join(lines)


def build_mcp_protocol() -> str:
    static = _load_static_template()
    lines = static.splitlines()

    # Componer el mapa una sola vez y reutilizarlo en las secciones vivas.
    try:
        from shared.core.engine.infrastructure.repositories import WorkflowRepository

        wf_map = compose_workflow_map(workflow_repo=WorkflowRepository())
    except Exception:
        return static

    try:
        tools_section = _build_tools_section()
        analysis_section = _build_analysis_section()
        workflow_section = _build_workflow_section(wf_map)
        capture_section = _build_capture_task_section(wf_map)
    except Exception:
        return static

    # Insertar sección 2 dinámica después de "2. TO ADVANCE..."
    # Insertar sección 3 dinámica después de "3. CODE ANALYSIS..."
    # Insertar workflow map después de la sección 5 completa.
    result: list[str] = []
    i = 0
    inserted_wf = False
    inserted_analysis = False
    while i < len(lines):
        line = lines[i]

        if not inserted_analysis and line.strip().startswith("3. CODE ANALYSIS = USE THE WALKER CPG"):
            result.append(line)
            i += 1
            while (
                i < len(lines)
                and lines[i].strip()
                and not lines[i].strip().startswith("4.")
                and not lines[i].strip().startswith("<!-- ANALYSIS_TOOLS -->")
            ):
                result.append(lines[i])
                i += 1
            if i < len(lines) and lines[i].strip().startswith("<!-- ANALYSIS_TOOLS -->"):
                if analysis_section:
                    result.append("")
                    result.extend(analysis_section.splitlines())
                    result.append("")
                i += 1
            inserted_analysis = True
            continue

        if not inserted_wf and line.strip().startswith("5. REUSE what exists"):
            result.append(line)
            i += 1
            while (
                i < len(lines)
                and lines[i].strip()
                and not lines[i].strip().startswith("6.")
            ):
                result.append(lines[i])
                i += 1
            result.append("")
            result.extend(workflow_section.splitlines())
            result.append("")
            inserted_wf = True
            continue

        if line.strip().startswith("<!-- ANALYSIS_TOOLS -->"):
            i += 1
            continue

        result.append(line)
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
