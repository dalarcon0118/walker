"""Legacy side-effects: import legacy trail to TraceGraph, create prompt files."""
from __future__ import annotations
import gzip
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any
from libs.shared_domain import ExecutionContext
from features.project import ProjectService


def import_legacy_trail(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    legacy_data = ctx.baggage.get("legacy_mapping", {})
    events = legacy_data.get("events", params.get("events", []))
    pid_dir = legacy_data.get("pid_dir", params.get("pid_dir", ""))
    project_root = legacy_data.get("project_root", "")
    if not project_root:
        try: project_root = ProjectService.resolve_paths(ctx.chain_dir or pid_dir).project_root if (ctx.chain_dir or pid_dir) else ""
        except Exception: project_root = pid_dir
    if not events:
        return {"ok": False, "imported": 0, "reason": "no events to import"}
    by_chain: dict[str, list[dict]] = {}
    for evt in events:
        by_chain.setdefault(evt.get("chain_id", "unknown"), []).append(evt)
    total_imported = 0
    traces_dir = os.path.join(project_root, "data", "traces")
    os.makedirs(traces_dir, exist_ok=True)
    for chain_id, chain_events in by_chain.items():
        trace_path = os.path.join(traces_dir, f"{chain_id}.json.gz")
        entries = []
        if os.path.exists(trace_path):
            try:
                with gzip.open(trace_path, "rt", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try: entries.append(json.loads(line))
                            except json.JSONDecodeError: pass
            except Exception: pass
        for evt in chain_events:
            entries.append({"event_id": str(uuid.uuid4())[:12], "parent_type": "legacy_trail", "parent_id": chain_id,
                            "event_type": "legacy_event", "tool": "legacy_mapper",
                            "timestamp": evt.get("at", datetime.now(timezone.utc).isoformat()),
                            "message": evt.get("event", ""), "input": {"distance": evt.get("distance", 0),
                            "has_loop": evt.get("has_loop", False), "has_calibration": evt.get("has_calibration", False),
                            "chain_id": chain_id}, "metadata": {"source": "legacy_migration",
                            "migrated_at": datetime.now(timezone.utc).isoformat()}})
            total_imported += 1
        try:
            payload = "\n".join(json.dumps(e, ensure_ascii=False) for e in entries)
            with gzip.open(trace_path, "wt", encoding="utf-8") as f: f.write(payload)
        except Exception as e:
            return {"ok": False, "imported": total_imported, "error": str(e)}
    return {"ok": True, "imported": total_imported, "chains_processed": list(by_chain.keys()), "traces_dir": traces_dir}


def create_legacy_prompts(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    legacy_data = ctx.baggage.get("legacy_mapping", {})
    prompts_needed = legacy_data.get("prompts_needed", params.get("prompts_needed", []))
    workflow_path = legacy_data.get("workflow_path", params.get("workflow_path", ""))
    project_root = legacy_data.get("project_root", params.get("project_root", ""))
    if not project_root and ctx.chain_dir:
        try: project_root = ProjectService.resolve_paths(ctx.chain_dir).project_root
        except Exception: pass
    if not prompts_needed:
        return {"ok": False, "created": 0, "reason": "no prompts to create"}
    # Buscar o crear prompts dir
    projects_dir = os.path.join(project_root, ".cognitive", "projects")
    prompts_base = ""
    if os.path.isdir(projects_dir):
        for pid in sorted(os.listdir(projects_dir)):
            wv3 = os.path.join(projects_dir, pid, "workflow.v3")
            if os.path.isdir(wv3):
                prompts_base = os.path.join(wv3, "prompts")
                os.makedirs(prompts_base, exist_ok=True)
                break
    if not prompts_base:
        prompts_base = os.path.join(project_root, "workflow.v3", "prompts")
        os.makedirs(prompts_base, exist_ok=True)
    created_files = []
    for prompt_info in prompts_needed:
        prompt_id = prompt_info.get("prompt_id", "")
        text = prompt_info.get("text", "")
        ptype = prompt_info.get("type", "user_input")
        if not prompt_id or not text: continue
        text_en = _translate_legacy_prompt(text)
        prompt_path = os.path.join(prompts_base, f"{prompt_id}.prompt.json")
        os.makedirs(os.path.dirname(prompt_path), exist_ok=True)
        prompt_data = {"id": prompt_id, "type": ptype, "version": 1,
                       "description": f"Migrated from legacy on_entry_message ({prompt_info.get('node', '?')})",
                       "template": text_en, "metadata": {"source": "legacy_migration",
                       "original_node": prompt_info.get("node", ""), "migrated_at": datetime.now(timezone.utc).isoformat()}}
        try:
            with open(prompt_path, "w") as f: json.dump(prompt_data, f, indent=2, ensure_ascii=False)
            created_files.append(prompt_path)
        except OSError as e:
            return {"ok": False, "created": len(created_files), "error": str(e)}
    return {"ok": True, "created": len(created_files), "files": created_files, "prompts_dir": prompts_base}


def save_converted_tasks(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    legacy_data = ctx.baggage.get("legacy_mapping", {})
    tasks = legacy_data.get("tasks", params.get("tasks", []))
    project_root = legacy_data.get("project_root", "")
    if not project_root and ctx.chain_dir:
        try: project_root = ProjectService.resolve_paths(ctx.chain_dir).project_root
        except Exception: pass
    if not tasks:
        return {"ok": False, "saved": 0, "reason": "no tasks to save"}
    data_dir = os.path.join(project_root, "data")
    os.makedirs(data_dir, exist_ok=True)
    output = {"version": "1.0", "source": "legacy_migration", "migrated_at": datetime.now(timezone.utc).isoformat(),
              "total_tasks": len(tasks), "tasks": tasks}
    output_path = os.path.join(data_dir, "imported-tasks.json")
    try:
        with open(output_path, "w") as f: json.dump(output, f, indent=2, ensure_ascii=False)
        return {"ok": True, "saved": len(tasks), "path": output_path}
    except OSError as e:
        return {"ok": False, "saved": 0, "error": str(e)}


def _translate_legacy_prompt(text: str) -> str:
    translations = {"Evalua la tarea actual": "Evaluate the current task", "PASO 1": "STEP 1",
                    "PASO 2": "STEP 2", "PASO 3": "STEP 3", "Tiene las 6 capas de contexto": "Has the 6 context layers",
                    "Si NO": "If NO", "Si SI": "If YES", "transicion": "transition",
                    "Actua como PO": "Act as Product Owner", "Actua como QA/Tester": "Act as QA/Tester",
                    "alcance": "scope", "objective": "objective", "predicates": "predicates",
                    "ESTAS EN ANALYSIS_GATE": "YOU ARE IN ANALYSIS_GATE",
                    "Ejecuta el analisis 3-capas": "Execute the 3-layer analysis",
                    "Q1": "Q1", "A1": "A1", "Q2": "Q2", "A2": "A2", "Q3": "Q3", "A3": "A3",
                    "Implementar tarea": "Implement task", "Diagnosticar refinamiento": "Diagnose refinement",
                    "refinamiento": "refinement", "INFORME DE TAREA": "TASK REPORT",
                    "INFORME DE IMPLEMENTACION": "IMPLEMENTATION REPORT", "Presenta al usuario": "Present to the user",
                    "Opciones del usuario": "User options", "PROCEDER": "PROCEED", "REFINAR": "REFINE",
                    "CANCELAR": "CANCEL", "descarta tarea": "discard task", "vuelve al sprint": "return to sprint",
                    "vuelve a ANALYSIS_GATE": "return to ANALYSIS_GATE", "vuelve a RESEARCH": "return to RESEARCH",
                    "vuelve a ADVANCE_TASK": "return to ADVANCE_TASK", "vuelve a PO_REVIEW": "return to PO_REVIEW",
                    "Crear sprint": "Create sprint", "Requiere goal, project-id, y array de tareas": "Requires goal, project-id, and task array",
                    "Dashboard sprint": "Sprint dashboard", "Workflow activo": "Active workflow",
                    "Verificar cierre": "Verify closure", "Cerrar sprint": "Close sprint",
                    "Verificar sprints": "Check sprints", "No hay sprints disponibles": "No sprints available",
                    "Usuario": "User", "gate": "gate", "feedback": "feedback", "razon": "reason",
                    "NOTA": "NOTE", "Arranca el flujo del skill": "Start the skill flow",
                    "No hay sesion activa": "No active session", "El skill acaba de arrancar": "The skill has just started",
                    "agregado por auto-migracion": "added by auto-migration"}
    result = text
    for es, en in translations.items(): result = result.replace(es, en)
    return result


def register(engine: Any) -> None:
    engine.register("import_legacy_trail", import_legacy_trail)
    engine.register("create_legacy_prompts", create_legacy_prompts)
    engine.register("save_converted_tasks", save_converted_tasks)
