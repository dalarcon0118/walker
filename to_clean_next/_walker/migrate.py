"""
migrate — build v4 workflow module

Extracted from walker.py.
"""

import os
import json
from utils import eprint, load_json, canonical_workflow_path

def _build_v4_workflow() -> dict:
    """Return the canonical v4 workflow with 5 nodes and sub-states.

    Loads from the canonical workflow.json (SSOT) with hardcoded fallback.
    The canonical file is at: .cognitive/projects/<project_id>/workflow.json

    NOTA: El fallback hardcoded es una copia estatica que puede
    desactualizarse. Mantener sincronizado con el workflow.json canonico.
    """
    wf_path = canonical_workflow_path()
    if os.path.exists(wf_path):
        data = load_json(wf_path)
        if data and data.get("version") in ("4.0", "4"):
            print("  [build] Usando workflow.json canonico (SSOT).")
            return data
        eprint("  [warn] workflow.json canonico invalido, usando fallback hardcoded.")
    else:
        eprint("  [warn] workflow.json canonico no encontrado, usando fallback hardcoded.")
    # Fallback: hardcoded v4 structure (completa, 5 nodos)
    return {
        "version": "4.0",
        "description": "Workflow v4 para delta-skill. 5 nodos con sub-estados secuenciales deterministas.",
        "initial_state": "SPRINT",
        "nodes": {
            "SPRINT": {
                "label": "Ciclo de vida del sprint",
                "description": "Gestiona el ciclo de vida del sprint: inicio, dashboard, edicion y cierre.",
                "sub_states": [
                    {"key": "INIT", "mode": "manual", "required_fields": [],
                     "on_entry_message": "Sistema iniciado. Usa transition start_task (si hay sprints) o crea uno nuevo."},
                    {"key": "CHECK", "mode": "auto", "auto_complete": "python3 projects/delta-skill/scripts/spring.py list",
                     "required_fields": []},
                    {"key": "CREATE", "mode": "manual", "required_fields": ["project_id", "goal", "tasks"],
                     "data_contract": {"fields": ["project_id", "goal", "tasks"]},
                     "on_entry_message": "Crea un sprint nuevo con submit --project_id <id> --goal <goal> --tasks <json>"},
                    {"key": "ACTIVE", "mode": "auto", "auto_complete": "python3 projects/delta-skill/scripts/spring.py status <chain>",
                     "required_fields": []},
                    {"key": "EDIT", "mode": "manual", "required_fields": ["action"],
                     "data_contract": {"fields": ["action", "task_id", "field", "value", "objective", "result_state", "type", "domain", "work_type", "role", "scrum_role"]},
                     "on_entry_message": "Modifica el plan del sprint. submit --action add-task|update-task|update-goal"},
                    {"key": "CLOSE_CHECK", "mode": "auto", "auto_complete": "python3 projects/delta-skill/scripts/spring.py health <chain>",
                     "required_fields": []},
                    {"key": "HANDLE_PENDING", "mode": "manual", "required_fields": ["decision"],
                     "on_entry_message": "Tareas pendientes. submit --decision backlog|cancel_pending|defer"},
                    {"key": "CLOSE", "mode": "auto", "auto_complete": "python3 projects/delta-skill/scripts/spring.py close <chain>",
                     "required_fields": []},
                ]
            },
            "ANALYZE": {
                "label": "Fase de analisis",
                "description": "Investigacion, analisis y aprobacion del contexto.",
                "sub_states": [
                    {"key": "GATHER", "mode": "auto", "auto_complete": "python3 projects/delta-skill/scripts/task.py research <chain> <task_id>",
                     "required_fields": []},
                    {"key": "ANALYZE", "mode": "manual", "required_fields": ["q1", "a1", "q2", "a2", "q3", "a3"],
                     "data_contract": {"fields": ["q1", "a1", "q2", "a2", "q3", "a3"]},
                     "on_entry_message": "Analisis 6->3 capas. submit con las 3 preguntas y respuestas."},
                    {"key": "REVIEW", "mode": "manual", "required_fields": ["decision"],
                     "on_entry_message": "Revisa el analisis. submit --decision approve|reject"},
                ]
            },
            "IMPLEMENT": {
                "label": "Fase de implementacion",
                "description": "Implementacion con USER_GATE y REPORT_GATE obligatorios.",
                "sub_states": [
                    {"key": "USER_GATE", "mode": "manual", "required_fields": ["decision"],
                     "data_contract": {"fields": ["decision", "notes", "reason"]},
                     "on_entry_message": "INFORME PRE-IMPLEMENTACION. submit --decision proceed|refine|cancel"},
                    {"key": "WORK", "mode": "auto", "auto_complete": "python3 projects/delta-skill/scripts/task.py advance <chain> <task_id>",
                     "required_fields": []},
                    {"key": "CODE_REVIEW", "mode": "manual", "required_fields": ["decision"],
                     "on_entry_message": "Code Review. submit --decision approve|reject --notes ..."},
                    {"key": "REPORT_GATE", "mode": "manual", "required_fields": ["decision"],
                     "data_contract": {"fields": ["decision", "notes", "reason"]},
                     "on_entry_message": "INFORME POST-IMPLEMENTACION. submit --decision proceed|refine|cancel"},
                ]
            },
            "VERIFY": {
                "label": "Fase de verificacion",
                "description": "QA y aceptacion del PO.",
                "sub_states": [
                    {"key": "QA", "mode": "manual", "required_fields": ["decision"],
                     "on_entry_message": "QA: submit --decision approve|reject --notes ..."},
                    {"key": "PO_ACCEPT", "mode": "manual", "required_fields": ["decision"],
                     "on_entry_message": "PO: submit --decision accept|reject --feedback ..."},
                ]
            },
            "REFINE": {
                "label": "Enrutamiento de refinamiento",
                "description": "Diagnostica y redirige al nodo correcto.",
                "sub_states": [
                    {"key": "DIAGNOSE", "mode": "manual", "required_fields": ["target"],
                     "on_entry_message": "submit --target analyze|implement|verify|sprint"},
                    {"key": "ROUTE", "mode": "auto", "required_fields": []},
                ]
            }
        },
        "edges": [
            {"from": "SPRINT", "to": "ANALYZE", "gate": "start_task", "description": "Iniciar analisis de tarea."},
            {"from": "SPRINT", "to": "SPRINT", "gate": "edit_plan", "description": "Editar plan del sprint.", "sub_state_gate": "EDIT"},
            {"from": "SPRINT", "to": "SPRINT", "gate": "close_sprint", "description": "Iniciar cierre.", "sub_state_gate": "CLOSE_CHECK"},
            {"from": "SPRINT", "to": "SPRINT", "gate": "back_to_active", "description": "Volver al dashboard.", "sub_state_gate": "ACTIVE"},
            {"from": "ANALYZE", "to": "IMPLEMENT", "gate": "analysis_approved", "description": "Analisis aprobado."},
            {"from": "ANALYZE", "to": "REFINE", "gate": "needs_refine", "description": "Refinar analisis."},
            {"from": "IMPLEMENT", "to": "VERIFY", "gate": "implementation_done", "description": "Implementacion completa."},
            {"from": "IMPLEMENT", "to": "REFINE", "gate": "needs_refine", "description": "Refinar implementacion."},
            {"from": "IMPLEMENT", "to": "SPRINT", "gate": "cancel", "description": "Tarea cancelada."},
            {"from": "VERIFY", "to": "SPRINT", "gate": "task_complete", "description": "Tarea completada."},
            {"from": "VERIFY", "to": "REFINE", "gate": "needs_refine", "description": "Refinar verificacion."},
            {"from": "REFINE", "to": "ANALYZE", "gate": "reroute_analyze", "description": "Redirigir a ANALYZE."},
            {"from": "REFINE", "to": "IMPLEMENT", "gate": "reroute_implement", "description": "Redirigir a IMPLEMENT."},
            {"from": "REFINE", "to": "VERIFY", "gate": "reroute_verify", "description": "Redirigir a VERIFY."},
            {"from": "REFINE", "to": "SPRINT", "gate": "reroute_sprint", "description": "Volver a SPRINT."},
        ]
    }
def _migrate_workflow(workflow_path: str, data: dict) -> bool:
    """Migrate v2/v3 workflow to v4 (5 nodes + sub-states).

    Returns True if migration was performed (caller should reload).
    """
    version = data.get("version", "1.0")
    if version in ("4.0", "4"):
        return False  # Already v4
    if not isinstance(data.get("nodes"), dict) or len(data["nodes"]) < 3:
        return False  # Not a valid workflow to migrate

    # Replace entire structure with v4
    v4 = _build_v4_workflow()
    data.clear()
    data.update(v4)

    try:
        with open(workflow_path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print("  [migrate] workflow.json migrado a v4 (5 nodos con sub-estados)")
        print("  [migrate] Los proyectos existentes usan el nuevo flujo determinista.")
    except (OSError, PermissionError) as e:
        eprint(f"  [migrate] Error guardando workflow.json: {e}")

    return True
def _emergency_workflow() -> dict:
    return {
        "version": "4.0",
        "description": "Workflow minimo de emergencia (v4)",
        "initial_state": "SPRINT",
        "nodes": {
            "SPRINT": {
                "label": "Dashboard",
                "description": "Ciclo de vida del sprint.",
                "sub_states": [
                    {"key": "CHECK", "mode": "auto", "auto_complete": "python3 projects/delta-skill/scripts/spring.py list", "required_fields": []},
                    {"key": "ACTIVE", "mode": "auto", "auto_complete": "python3 projects/delta-skill/scripts/spring.py status <chain>", "required_fields": []},
                    {"key": "CLOSE", "mode": "auto", "auto_complete": "python3 projects/delta-skill/scripts/spring.py close <chain>", "required_fields": []},
                ]
            }
        },
        "edges": []
    }
def _presprint_workflow() -> dict:
    return {
        "version": "4.0",
        "description": "Workflow pre-sprint (v4). Sin sprint activo.",
        "initial_state": "SPRINT",
        "nodes": {
            "SPRINT": {
                "label": "Pre-sprint",
                "description": "Sin sprint activo.",
                "sub_states": [
                    {"key": "INIT", "mode": "manual", "required_fields": [],
                     "on_entry_message": "No hay sprint activo. Crea un sprint con spring.py create --project-id <id> --goal ... --tasks ..."},
                    {"key": "CHECK", "mode": "auto", "auto_complete": "python3 projects/delta-skill/scripts/spring.py list", "required_fields": []},
                ]
            }
        },
        "edges": []
    }