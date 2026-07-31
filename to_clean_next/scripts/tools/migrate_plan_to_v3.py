#!/usr/bin/env python3
"""Migrate delta-skill plan.json to schema v3."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from validate_schema import _validate_schema
except ImportError:
    _validate_schema = None

BASE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = BASE_DIR / "schema" / "plan.schema.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_task(task: dict, idx: int) -> dict:
    """Normalize one task dict to schema v3."""
    existing_id = task.get("task_id")
    if existing_id and existing_id.isupper():
        task.setdefault("task_id", existing_id)
    else:
        task.setdefault("task_id", f"TASK-{idx + 1:03d}")
    task.setdefault("type", "feature")
    task.setdefault("domain", "core")
    task.setdefault("role", "core")
    task.setdefault("scrum_role", "dev")
    task.setdefault("work_type", "refactor")
    task.setdefault("objective", "")
    task.setdefault("result_state", "")
    task.setdefault("task_description", task.get("objective", "")[:80])
    task.setdefault("impacted_artifacts", {"files": []})
    task.setdefault("validation", {"predicates": []})
    task.setdefault("preconditions", [])
    task.setdefault("acceptance_criteria", [])
    task.setdefault("quality_gates", [])
    task.setdefault("risks", {"curvature": 0.2})
    task.setdefault("loop_verification", [])
    task.setdefault("loop_acceptance", "all")
    task.setdefault("decisions", [])
    task.setdefault("refinement", {"attempts": 0, "dead_ends": []})
    task.setdefault("importer_files", 0)
    task.setdefault("depends_on", [])
    task.setdefault("tags", [])
    task.setdefault("task_context", {
        "observation": "Contexto generado.",
        "structure": "Contexto generado.",
        "behavior": "Contexto generado.",
        "causality": "Contexto generado.",
        "purpose": "Contexto generado.",
        "principles": "Contexto generado.",
    })
    return task


def migrate(plan_path: str) -> bool:
    path = Path(plan_path)
    if not path.exists():
        print(f"❌ No existe: {plan_path}")
        return False

    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        print("❌ plan.json no es un objeto JSON")
        return False

    # Normalize top-level wrapper
    if "plan" not in data:
        data = {"plan": data}

    pl = data.setdefault("plan", {})

    # meta
    meta = sprint_state.setdefault("meta", {}), {})
    meta.setdefault("chain_id", path.parent.name)
    meta.setdefault("generated_at", _now_iso())
    meta.setdefault("version", "3.0")
    project_id_from_chain = path.parent.name if path.parent.name != "delta" else path.parent.parent.name
    meta.setdefault("source", f"{project_id_from_chain}:migrate")
    meta.setdefault("type", "plan")

    # project
    project = pl.setdefault("project", {})
    project.setdefault("project_id", project_id_from_chain)
    project.setdefault("project_type", "backend")
    project.setdefault("domains", ["core"])
    project.setdefault("tags", ["delta"])

    # goal
    objetivo = pl.setdefault("goal", {})
    objetivo.setdefault("description", "")
    objetivo.setdefault("predicates", {"must_exist": [], "must_not_exist": [], "invariants": []})
    objetivo.setdefault("path_summary", "")
    objetivo.setdefault("signatures", [])
    objetivo.setdefault("done_when", [])

    # Normalize tasks
    tasks = pl.setdefault("tasks", [])
    for i, t in enumerate(tasks):
        _ensure_task(t, idx=i)  # pass i for id generation, but preserve existing IDs
        # Uppercase sub-tasks to match schema pattern ^TASK-[A-Z0-9-]+$
        tid = t.get("task_id", "")
        if tid and not tid.isupper():
            t["task_id"] = tid.upper()
        # Ensure validation.predicates is a list of strings or objects with type
        preds = t.get("validation", {}).get("predicates", [])
        if preds and not t.get("loop_verification"):
            t["loop_verification"] = preds

    pl["tasks"] = tasks

    # Write atomically
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp_path.replace(path)

    print(f"✅ Migrado: {plan_path}")
    print(f"   tasks: {len(tasks)}")

    # Validate if possible
    if _validate_schema and SCHEMA_PATH.exists():
        valid = _validate_schema(str(path), "plan.schema.json")
        if valid:
            print("✅ Validación de schema: OK")
        else:
            print("❌ Validación de schema: FAIL")
            return False

    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: migrate_plan_to_v3.py <plan.json>")
        sys.exit(2)
    ok = migrate(sys.argv[1])
    sys.exit(0 if ok else 1)
