"""
infrastructure/output.py — Result formatting for display.

SRP: Single responsibility = convert dict result to text.
SSOT: All output formats live here.

Soporta el contrato Response REST: formatea message, data y next_actions
de forma coherente para que el agente consumidor siempre sepa qué pasó
y qué hacer después.
"""

from __future__ import annotations

import json
from typing import Any

from ..infrastructure.contracts import Response


def help_output(data: dict) -> None:
    """Format help: protocol + commands + tasks (discoverables) + indices (discoverables)."""
    # Protocolo obligatorio para el agente
    protocol = data.get("protocol", "")
    if protocol:
        print("=" * 70)
        for line in protocol.strip().split("\n"):
            print(f"  {line.strip()}")
        print("=" * 70)
        print()

    # Sprint lifecycle
    sl = data.get("sprint_lifecycle", {})
    if sl:
        print(f"  {sl.get('description', '')}")
        print()
        print("  Como crear un sprint:")
        for line in sl.get("como_crear_un_sprint", "").split("\n"):
            print(f"    {line.strip()}")
        print()
        print("  Nodos del workflow:")
        for n in sl.get("nodos_del_workflow", []):
            nodo = n.get("nodo", "?")
            desc = n.get("desc", "")
            task = n.get("task", "")
            if task:
                print(f"    {nodo:<30} {desc} (task: {task})")
            else:
                print(f"    {nodo:<30} {desc}")
        trans = sl.get("transiciones", "")
        if trans:
            print()
            for line in trans.split("\n"):
                print(f"    {line.strip()}")
        print()

    # Comandos basicos (fijos del CLI)
    print("  Available commands:")
    for cmd in data.get("commands", []):
        name = cmd.get("name", "?")
        desc = cmd.get("desc", "")
        usage = cmd.get("usage", "")
        print(f"    {name:<12} {desc}")
        if usage:
            print(f"               {usage}")

    # Tasks registradas (dinamicas, desde TaskRegistry.discover())
    tc = data.get("task_catalog", {})
    tasks = tc.get("tasks", [])
    if tasks:
        print()
        print(f"  ── Tasks Registradas ({tc.get('total', len(tasks))} tasks) ──")
        print("  (Cada tarea se auto-documenta. Ejecútala sin body para ver ayuda)")
        for t in tasks:
            name = t.get("name", "?")
            desc = t.get("description", "")
            usage = t.get("usage", "")
            example = t.get("example", "")
            print(f"    {name:<30} {desc}")
            if usage:
                print(f"    {'':<30} uso: {usage}")
            if example:
                print(f"    {'':<30} ej:  {example}")
            use_cases = t.get("use_cases", {})
            if use_cases:
                for role, cases in use_cases.items():
                    for c in cases[:2]:  # max 2 por rol
                        print(f"    {'':<30} {role}: {c[:80]}")

    # Indices de CognitiveGraph (descubiertos via schema)
    indices = data.get("indices", [])
    if indices:
        print()
        print("  ── CognitiveGraph Indices ──")
        print('  (Usa kg_schema \'{"index":"<nombre>"}\' para ver campos y tipos)')
        for idx in indices:
            name = idx.get("name", "?")
            desc = idx.get("description", "")
            fields = idx.get("fields", [])
            types = idx.get("types", [])
            docs = idx.get("total_docs", 0)
            print(f"    {name:<10} {desc}")
            if fields:
                print(f"    {'':<10} campos: {', '.join(fields[:6])}")
            print(
                f"    {'':<10} docs: {docs}, tipos: {', '.join(types[:4]) if types else '?'}"
            )


def projects_output(data: dict) -> None:
    """Format project list."""
    for p in data.get("projects", []):
        sprints = p.get("active_sprints", [])
        status = p.get("status", "?")
        active = f" ({len(sprints)} active)" if sprints else ""
        print(f"  {p['project_id']:<30} [{status}]{active}")
    prompt = data.get("prompt", "")
    if prompt:
        print(prompt)


def status_output(data: dict) -> None:
    """Format workflow status result."""
    warning = data.get("_warning", "")
    if warning:
        print(f"  {warning}")
        print()
    print(f"  Project: {data.get('project_id', '?')}")
    print(f"  Node:    {data.get('node', '?')}.{data.get('sub_state', '?')}")
    print(f"  History: {' -> '.join(data.get('history', []))}")

    prompt = data.get("prompt", "")
    if prompt:
        print()
        print("  ── Instructions ──")
        for line in prompt.strip().split("\n"):
            print(f"  {line.strip()}")
        print()

    required = data.get("required_fields", [])
    if required:
        print(f"  Required fields: {', '.join(required)}")

    gates = data.get("available_gates", [])
    if gates:
        print(f"  Available gates ({len(gates)}):")
        for g in gates:
            print(f"    - {g.get('name', '?')}: {g.get('description', '')}")


def health_output(data: dict) -> None:
    """Format project health status."""
    pid = data.get("project_id", "?")
    proj = data.get("project", {})
    checks = data.get("checks", {})
    warnings = data.get("warnings", [])
    sprint = data.get("sprint")

    print(f"  Project: {pid}")
    if proj:
        name = proj.get("name", pid)
        lang = proj.get("language", "")
        framework = proj.get("framework", "")
        print(f"  Name:     {name}")
        if lang:
            print(f"  Language: {lang}")
        if framework:
            print(f"  Framework: {framework}")

    print()
    print("  ── Memories ──")
    _print_checks(checks)

    if sprint is not None:
        print()
        print("  ── Sprint ──")
        print(f"  Node: {sprint.get('node', '?')}.{sprint.get('sub_state', '?')}")
        _print_sprint_progress(sprint)
        _print_sprint_prompt(sprint)
        _print_sprint_gates(sprint)
    else:
        print("  ℹ️  No active sprint")

    if warnings:
        print()
        print(f"  ── {len(warnings)} Warnings ──")
        for w in warnings:
            print(f"  ⚠️  {w}")

    # ── Proactive next steps ──
    _print_next_steps(data.get("next_steps", []))


def _print_checks(checks: dict) -> None:
    """Imprime el estado de las memorias (files/cpg/ckg)."""
    for check_name in ["files", "cpg", "ckg"]:
        status = checks.get(check_name, "?")
        icon = (
            "✅"
            if status == "up to date"
            or (check_name in ("cpg", "ckg") and "nodes" in status)
            else "⚠️"
            if "error" in status
            or "not" in status
            or "empty" in status
            or "changed" in status
            else "✅"
        )
        print(f"  {icon} {check_name.upper()}: {status}")


def _print_sprint_progress(sprint: dict) -> None:
    """Imprime el GPS de progreso del grafo."""
    progress = sprint.get("graph_progress", {})
    if not progress:
        return
    pos = progress.get("position", "?")
    hub_ok = progress.get("can_return_to_hub", False)
    dist = progress.get("distance_to_terminal", -1)
    path = progress.get("path_to_terminal", [])
    term = progress.get("terminal", "")
    print(
        f"  📍 {pos} | puede volver al hub: {'SI' if hub_ok else 'NO'}"
        f" | distancia al final: {dist if dist >= 0 else '?'} hops"
    )
    if path:
        print(
            f"     ruta sugerida: {' → '.join(path)}"
            + (f" → {term}" if term and path[-1] != term else "")
        )
    cycles = progress.get("cycles_in_graph", 0)
    if cycles:
        print(
            f"     ciclos del workflow: {cycles} (REFINE es el hub de rework)"
        )


def _print_sprint_prompt(sprint: dict) -> None:
    """Imprime las instrucciones del sprint."""
    prompt = sprint.get("prompt", "")
    if prompt:
        print("  ── Instructions ──")
        for line in prompt.strip().split("\n"):
            print(f"  {line.strip()}")

    required = sprint.get("required_fields", [])
    if required:
        print(f"  Required fields: {', '.join(required)}")


def _print_sprint_gates(sprint: dict) -> None:
    """Imprime las gates disponibles del sprint."""
    gates = sprint.get("gates", [])
    if gates:
        for g in gates:
            print(f"    gate: {g.get('name', '?')} -> {g.get('description', '')}")


def _print_next_steps(next_steps: list) -> None:
    """Imprime los next steps priorizados."""
    if not next_steps:
        return
    priority_labels = {
        1: "🔴 High",
        2: "🟠 Medium-High",
        3: "🟡 Medium",
        4: "🟢 Low",
        5: "⚪ Optional",
    }
    print()
    print("  ── Next Steps (Prioritized) ──")
    for step in next_steps:
        pri = step.get("priority", 99)
        action = step.get("action", "")
        reason = step.get("reason", "")
        command = step.get("command", "")

        print(f"  {priority_labels.get(pri, '⚪')} [{pri}] {action}")
        print(f"         → {reason}")
        if command:
            print(f"         Run: {command}")


def project_info_output(data: dict) -> None:
    """Format all project information."""
    pid = data.get("project_id", "?")
    print(f"  Project: {pid}")
    print(f"  Path: {data.get('repo_path', '?')}")

    name = data.get("name", pid)
    lang = data.get("language", "")
    framework = data.get("framework", "")
    print(f"  Name: {name}")
    if lang:
        print(f"  Language: {lang}")
    if framework:
        print(f"  Framework: {framework}")

    reg = data.get("registered_at", "")
    last = data.get("last_accessed", "")
    if reg:
        print(f"  Registered: {reg[:19]}")
    if last:
        print(f"  Last access: {last[:19]}")

    print()
    print("  ── Memories ──")
    if "cpg_nodes" in data:
        print(f"  CPG: {data['cpg_nodes']} nodes, {data.get('cpg_edges', 0)} edges")
    if "ckg_docs" in data:
        print(f"  CKG: {data['ckg_docs']} documents")
    if "delta_tasks" in data:
        print(f"  Delta: {data['delta_tasks']} indexed tasks")

    watcher = data.get("watcher", {})
    if watcher:
        fw = watcher.get("files_changed", [])
        status = "clean" if not fw else f"{len(fw)} pending changes"
        print(f"  Watcher: {status}")

    if data.get("active_sprint"):
        print("  Sprint: active")
    else:
        print("  Sprint: none")

    indices = data.get("cognitive_indices", [])
    if indices:
        print(f"  CognitiveGraph indices: {', '.join(indices)}")


def submit_output(data: dict) -> None:
    sub_state = data.get("sub_state", "?")
    field_count = data.get("field_count", 0)
    if not data.get("ok", True) or data.get("error"):
        err = data.get("error") or data.get("message", "Unknown error")
        print(f"  Submit failed: {err}")
        details = data.get("errors")
        if details:
            print(f"  Details: {json.dumps(details, ensure_ascii=False)}")
        return
    print(f"  Submitted to {sub_state}: {field_count} fields")


def transition_output(data: dict) -> None:
    print(
        f"  {data.get('from_node', '?')} -> {data.get('to_node', '?')} (via {data.get('gate', '?')})"
    )


def task_output(data: dict) -> None:
    ok = data.get("ok", False)
    print(f"  Task: {data.get('action', '?')} -> ok={ok}")
    result = data.get("result")
    if result:
        # Mostrar error si existe, siempre visible
        error = result.get("error", "") if isinstance(result, dict) else ""
        if error and not ok:
            print(f"  Error: {error}")
        # Mostrar siguiente accion si existe
        next_step = result.get("next_step", "") if isinstance(result, dict) else ""
        if next_step:
            print(f"  Next: {next_step}")
        # Mostrar resultado completo
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif not ok:
        print(
            "  Error: Task failed without details. Check chain_dir or task parameters."
        )


def prepare_output(data: dict) -> None:
    """Format prepare result."""
    detected = data.get("detected", {})
    print(f"  Project: {data.get('project_id', '?')}")
    print(f"  Path: {data.get('path', '?')}")
    print(f"  Cognitive dir: {data.get('cognitive_dir', '?')}")
    print(f"  Project.json: {data.get('project_json', '?')}")
    print(f"  Global registry: {data.get('registered_in', 'not registered')}")
    print(f"  Detected language: {detected.get('language', '?')}")
    print(f"  Detected framework: {detected.get('framework', '?')}")
    print(f"  Detected code paths: {detected.get('code_paths', [])}")
    if data.get("cpg_built", 0) > 0:
        print(f"  CPG built: {data.get('cpg_built')} nodes")
    if data.get("watcher_installed"):
        print("  Watcher: installed ✅")
    warnings = data.get("setup_warnings", data.get("_warnings", []))
    if warnings:
        print()
        print(f"  ── {len(warnings)} Setup Warnings ──")
        for w in warnings:
            print(f"  ⚠️  {w}")
    steps = data.get("next_steps", [])
    if steps:
        print()
        print("  Next steps:")
        for s in steps:
            print(f"    → {s}")


def changes_output(data: dict) -> None:
    """Format sprint status with code changes detected."""
    changes = data.get("changes", {})
    total = changes.get("total_changes", 0)
    if total > 0:
        print(f"  ⚠️  {total} file(s) changed since last session")
        print(f"  CPG status: {changes.get('cpg_status', '?')}")
        print(f"  CKG status: {changes.get('ckg_status', '?')}")
        for f in changes.get("files_changed", [])[:5]:
            print(f"    - {f}")
        if total > 5:
            print(f"    ... and {total - 5} more")
    print(f"  Node: {data.get('node', '?')}.{data.get('sub_state', '?')}")
    print(f"  History: {' -> '.join(data.get('history', []))}")


def routes_output(data: dict) -> None:
    """Format REST endpoint list (autodoc estilo FastAPI /docs)."""
    routes = data.get("routes", [])
    print(f"  ── REST Endpoints ({len(routes)}) ──")
    print(f"  {'METHOD':<12} {'PATH':<40} DESCRIPTION")
    print(f"  {'-' * 12}  {'-' * 40}  {'-' * 30}")
    for r in sorted(routes, key=lambda x: (x["pattern"], x["method"])):
        method = r.get("method", "?")
        pattern = r.get("pattern", "?")
        desc = r.get("description", "")
        print(f"  {method:<12} {pattern:<40} {desc}")


def error_output(message: str, exit_code: int = 1) -> int:
    print(f"  Error: {message}")
    return exit_code


def no_sprint_output(data: dict) -> None:
    warning = data.get("_warning", "")
    if warning:
        print(f"  {warning}")
        print()
    print(f"  Project '{data.get('project_id', '?')}' has no active sprint")


# Result type → formatter map (SSOT)
def mcp_config_output(data: dict) -> None:
    """Muestra la config MCP (el usuario la necesita para copiar/pegar)."""
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print()


OUTPUT_MAP: dict[str, Any] = {
    "help": help_output,
    "projects_list": projects_output,
    "project_health": health_output,
    "project_info": project_info_output,
    "sprint_status": status_output,
    "sprint_status_with_changes": changes_output,
    "no_sprint": no_sprint_output,
    "submit_result": submit_output,
    "transition_result": transition_output,
    "task_result": task_output,
    "prepare_result": prepare_output,
    "routes_list": routes_output,
    "mcp_config": mcp_config_output,
}


def format_result(result: Response | dict) -> int:
    """Format a result (Response REST o dict legacy) y retorna exit code.

    Analogía Express: res.status(code).json({body, links}).
    El Response se renderiza SIEMPRE de forma coherente:
      1. ✅/❌ message (qué pasó)
      2. detalle formateado según type (data)
      3. ── Next Actions ── (qué hacer ahora, con comandos exactos)
    """
    if isinstance(result, Response):
        return _format_response(result)
    # ── Legacy dict (compatibilidad) ──
    result_type = result.get("type", "")
    if result_type == "error":
        return error_output(result.get("message", "Unknown error"))
    formatter = OUTPUT_MAP.get(result_type)
    if formatter:
        formatter(result)
        status = result.get("status_code", 200)
        return 1 if status >= 400 else 0
    # Fallback: raw JSON
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _format_response(resp: Response) -> int:
    """Renderiza un Response REST estándar."""
    if resp.status_code >= 400 or resp.type == "error":
        print(f"  ❌ {resp.message}")
        if resp.errors:
            print(f"  Details: {json.dumps(resp.errors, ensure_ascii=False)}")
        _print_next_actions(resp.next_actions)
        return 1

    print(f"  ✅ {resp.message}")
    print()
    formatter = OUTPUT_MAP.get(resp.type)
    if formatter:
        formatter(resp.data)
    _print_next_actions(resp.next_actions)
    return 0


def _print_next_actions(actions: list) -> None:
    """Imprime la sección 'Next Actions' con comandos exactos."""
    if not actions:
        return
    print()
    print("  ── Next Actions ──")
    for i, a in enumerate(actions, 1):
        if isinstance(a, dict):
            cmd = a.get("command", "")
            reason = a.get("reason", "")
        else:
            cmd = getattr(a, "command", "")
            reason = getattr(a, "reason", "")
        print(f"  {i}. {cmd}")
        if reason:
            print(f"     → {reason}")
