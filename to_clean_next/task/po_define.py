"""task/po_define.py — PO task definition prompt for Kilo."""

from __future__ import annotations

import json
from pathlib import Path


def _load_schema() -> dict:
    base = Path(__file__).resolve().parent.parent.parent
    schema_path = base / "schema" / "plan.schema.json"
    if schema_path.exists():
        return json.loads(schema_path.read_text())
    return {}


def _load_system_prompt() -> str:
    from prompt import PromptManager

    try:
        return PromptManager("").render_template("po-define.md", {})
    except Exception:
        return "Actúa como PO y completá la tarea con datos ejecutables."


def _build_task_schema_summary(task_schema: dict) -> str:
    properties = task_schema.get("properties", {})
    summary = {
        "required": task_schema.get("required", []),
        "fields": {},
    }
    for k, v in properties.items():
        field_info = {
            "type": v.get("type"),
            "description": v.get("description", ""),
        }
        if "enum" in v:
            field_info["enum"] = v["enum"]
        if "minLength" in v:
            field_info["minLength"] = v["minLength"]
        if "pattern" in v:
            field_info["pattern"] = v["pattern"]
        if v.get("items", {}).get("oneOf"):
            field_info["options"] = [
                opt.get("properties", {}).get("type", {}).get("enum", [])
                for opt in v["items"]["oneOf"]
                if opt.get("properties", {}).get("type", {}).get("enum")
            ]
        summary["fields"][k] = field_info
    return json.dumps(summary, indent=2, ensure_ascii=False)
