"""spring/list.py — List sprints"""

from __future__ import annotations
import sys
from pathlib import Path

_THIS_DIR = str(Path(__file__).parent.parent)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
try:
    from _internal.projects import (
        COGNITIVE_DIR as _COGNITIVE_DIR,
        PROJECTS_DIR as _PROJECTS_DIR,
        DELTA_DIR as _DELTA_DIR,
    )
except ImportError:
    _COGNITIVE_DIR = ".cognitive"
    _PROJECTS_DIR = "projects"
    _DELTA_DIR = "delta"


import os
import sys
from pathlib import Path
from .common import find_project_root, load_json


def cmd_list(args: list[str]) -> int:
    """List all sprints across all projects."""
    root = find_project_root()
    cog_dir = os.path.join(root, _COGNITIVE_DIR)

    sprints = []

    # Scan legacy: .cognitive/delta/*/sprint-state.json
    legacy_delta = os.path.join(cog_dir, _DELTA_DIR)
    if os.path.isdir(legacy_delta):
        for entry in os.listdir(legacy_delta):
            sprint_state_path = os.path.join(legacy_delta, entry, "sprint-state.json")
            if os.path.isfile(sprint_state_path):
                data = load_json(sprint_state_path)
                if data:
                    # v4: tasks en root; v3: progress.task_states
                    tasks = data.get("tasks") or data.get("progress", {}).get(
                        "task_states", {}
                    )
                    status = (
                        data.get("state")
                        or data.get("sprint", {}).get("status")
                        or data.get("walker", {}).get("state")
                        or data.get("progress", {})
                        .get("meta", {})
                        .get("status", "unknown")
                    )
                    sprints.append(
                        {
                            "chain_id": entry,
                            "project_id": "(legacy)",
                            "status": status,
                            "tasks": len(tasks),
                            "path": os.path.join(legacy_delta, entry),
                        }
                    )

    # Scan multi-project: .cognitive/projects/*/delta/*/sprint-state.json
    proj_dir = os.path.join(cog_dir, _PROJECTS_DIR.lstrip("/"))
    if os.path.isdir(proj_dir):
        for pid in os.listdir(proj_dir):
            delta_dir = os.path.join(proj_dir, pid, _DELTA_DIR)
            if not os.path.isdir(delta_dir):
                continue
            for entry in os.listdir(delta_dir):
                sprint_state_path = os.path.join(delta_dir, entry, "sprint-state.json")
                if os.path.isfile(sprint_state_path):
                    data = load_json(sprint_state_path)
                    if data:
                        # v4: tasks en root; v3: progress.task_states
                        tasks = data.get("tasks") or data.get("progress", {}).get(
                            "task_states", {}
                        )
                        status = (
                            data.get("state")
                            or data.get("sprint", {}).get("status")
                            or data.get("walker", {}).get("state")
                            or data.get("progress", {})
                            .get("meta", {})
                            .get("status", "unknown")
                        )
                        sprints.append(
                            {
                                "chain_id": entry,
                                "project_id": pid,
                                "status": status,
                                "tasks": len(tasks),
                                "path": os.path.join(delta_dir, entry),
                            }
                        )

    if not sprints:
        print("📭 No hay sprints registrados.")
        return 0

    # Sort by chain_id (roughly chronological)
    sprints.sort(key=lambda s: s["chain_id"])

    print("=" * 70)
    print(f"📋 SPRINTS ({len(sprints)})")
    print("=" * 70)
    print(f"  {'Proyecto':<25} {'Chain ID':<45} {'Estado':<12} {'Tareas':<8}")
    print(f"  {'─' * 25} {'─' * 45} {'─' * 12} {'─' * 8}")
    icon_map = {"active": "🟢", "closed": "🔒", "suspended": "⏸️", "pending": "⏳"}
    for s in sprints:
        icon = icon_map.get(s["status"], "❓")
        print(
            f"  {s['project_id']:<25} {s['chain_id']:<45} {icon} {s['status']:<10} {s['tasks']:<8}"
        )
    print("─" * 70)
    print()
    print("📋 Para ver detalle de un sprint:")
    print("  python3 scripts/spring.py status <chain_dir>")
    print()
    return 0
