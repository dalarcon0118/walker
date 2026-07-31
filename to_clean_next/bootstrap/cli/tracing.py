"""cli/tracing.py — Handlers de hooks de tracing.

Responsabilidad: comandos llamados desde hooks de agente (delta-guardian.ts).
register_file, register_message, trace_log escriben eventos en TraceGraph
y opcionalmente actualizan el estado del sprint.
"""

from __future__ import annotations
import json
import os
from datetime import datetime, timezone

from libs.cognitive_graph import CognitiveGraph
from libs.path_resolver import (
    get_project_dir,
    get_delta_dir,
    get_sprint_dir,
)


def _resolve_graph(project_id: str = "") -> CognitiveGraph:
    """Resuelve el CognitiveGraph (trace index) para un proyecto.

    Usa get_project_dir como chain_dir porque los datos (trace.json.gz)
    viven en <project_dir>/data/.
    """
    chain_dir = str(get_project_dir(project_id))
    return CognitiveGraph(chain_dir=chain_dir)


def cmd_register_file(args: list[str]) -> int:
    """Registra un archivo modificado en el TraceGraph.

    Uso: register_file <project_id> <file_path> [action] [summary]
    """
    if len(args) < 2:
        print("USO: register_file <project_id> <file_path> [action] [summary]")
        return 1
    project_id = args[0]
    file_path = args[1]
    action = args[2] if len(args) > 2 else "modify"
    summary = args[3] if len(args) > 3 else ""
    graph = _resolve_graph(project_id)
    session_id = os.environ.get("WALKER_SESSION_ID", "cli")
    node = graph.store(
        "trace",
        {
            "id": f"file:{session_id}:{datetime.now(timezone.utc).isoformat()}",
            "type": "file_change",
            "session_id": session_id,
            "action": action,
            "file_path": file_path,
            "summary": summary,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
    print(json.dumps({"status": "ok", "trace_id": node.get("id", "")}))
    return 0


def cmd_register_message(args: list[str]) -> int:
    """Registra un mensaje de agente en el TraceGraph.

    Uso: register_message <project_id> <role> <content>
    """
    if len(args) < 3:
        print("USO: register_message <project_id> <role> <content>")
        return 1
    project_id = args[0]
    role = args[1]
    content = args[2]
    graph = _resolve_graph(project_id)
    session_id = os.environ.get("WALKER_SESSION_ID", "cli")
    node = graph.store(
        "trace",
        {
            "id": f"msg:{session_id}:{datetime.now(timezone.utc).isoformat()}",
            "type": "message",
            "session_id": session_id,
            "role": role,
            "content": content[:500],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
    print(json.dumps({"status": "ok", "trace_id": node.get("id", "")}))
    return 0


def cmd_trace_log(args: list[str]) -> int:
    """Registra un evento en el TraceGraph y opcionalmente actualiza el sprint.

    Uso: trace_log <project_id> <event_type> <key=value> [key=value ...]

    Si event_type = 'trace_push', actualiza el contador de pushes en el sprint.
    """
    if len(args) < 3:
        print("USO: trace_log <project_id> <event_type> <key=value> [key=value ...]")
        return 1
    project_id = args[0]
    event_type = args[1]
    kv_pairs = {}
    for a in args[2:]:
        if "=" in a:
            k, v = a.split("=", 1)
            kv_pairs[k] = v

    graph = _resolve_graph(project_id)
    session_id = os.environ.get("WALKER_SESSION_ID", "cli")
    agent_id = os.environ.get("WALKER_AGENT_ID", "unknown")

    node = graph.store(
        "trace",
        {
            "id": f"{event_type}:{session_id}:{datetime.now(timezone.utc).isoformat()}",
            "type": event_type,
            "session_id": session_id,
            "agent_id": agent_id,
            **kv_pairs,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

    # Si es trace_push, actualizar contador en sprint-state
    if event_type == "trace_push":
        _update_trace_push_count(project_id)

    print(json.dumps({"status": "ok", "trace_id": node.get("id", "")}))
    return 0


def _update_trace_push_count(project_id: str) -> None:
    """Actualiza el contador de trace_pushes en el sprint activo."""
    delta_dir = get_delta_dir(project_id)
    if not delta_dir.is_dir():
        return

    try:
        for s in sorted(os.listdir(str(delta_dir)), reverse=True):
            state_file = get_sprint_dir(project_id, s) / "sprint-state.json"
            if state_file.exists():
                with open(str(state_file)) as f:
                    state = json.load(f)
                if state.get("sprint", {}).get("status") == "active":
                    pushes = state.get("trace_pushes", 0) + 1
                    state["trace_pushes"] = pushes
                    with open(str(state_file), "w") as f:
                        json.dump(state, f, indent=2)
                    break
    except Exception:
        pass
