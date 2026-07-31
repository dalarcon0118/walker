"""spring/plan.py — Plan management commands (add-task, update-task, etc.)"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from .common import (
    _ensure_v3_defaults,
    _now_iso,
    _validate_schema,
    _validate_task_fields,
    eprint,
    load_json,
    write_json,
)
from utils import (
    _parse_args,
    _EDITABLE_FIELDS as _EDITABLE_TASK_FIELDS,
    _BASE_DIR,
    _SCRIPT_DIR,
)
from _internal.advance import _kv_save


def cmd_add_task(args: list[str]) -> int:
    """Add a new task to an existing sprint.

    Auto-generates the task_id (TASK-NNN), adds the task to plan.json,
    creates a pending entry in sprint-state.json task_states, and adds a trail entry.
    NOTA: Task predicates NO se agregan a goal (TASK-025).

    Usage:
      spring.py add-task <chain_dir> --task '{"objective":"...","type":"feature",...}'

    The --task JSON supports all plan.schema.json task fields except
    task_id (auto-generated). At minimum requires: objective, type,
    domain, result_state, validation.predicates, impacted_artifacts.files, risks.curvature.
    """
    positional, flags = _parse_args(args)
    if len(positional) != 1:
        eprint("❌ Uso: spring.py add-task <chain_dir> --task '{}'")
        eprint("   --task JSON con campos: objective, type, domain, result_state,")
        eprint(
            "           validation.predicates, impacted_artifacts.files, risks.curvature"
        )
        eprint("   --task-file <path>     Alternativa: leer --task desde archivo JSON")
        return 2

    chain_dir = os.path.abspath(positional[0])
    plan_path = os.path.join(chain_dir, "plan.json")
    sprint_state_path = os.path.join(chain_dir, "sprint-state.json")

    plan = load_json(plan_path)
    progress = load_json(sprint_state_path)
    if not plan or not progress:
        eprint(f"❌ Sprint no encontrado en {chain_dir}")
        return 2

    pl = plan.get("plan", plan)
    prog = progress  # sprint-state.json data (no nested .progress key)
    chain_id = pl.get("meta", {}).get("chain_id", os.path.basename(chain_dir))
    project_id = pl.get("project", {}).get("project_id", "")

    # Sprint must be active
    status = prog.get("meta", {}).get("status", "unknown")
    if status == "closed":
        eprint(f"❌ Sprint '{chain_id}' ya está cerrado. No se pueden agregar tareas.")
        return 2

    # Parse --task JSON (or --task-file <path>)
    task_json = flags.get("task", "")
    task_file = flags.get("task-file", "")
    if task_file:
        if not os.path.exists(task_file):
            eprint(f"❌ --task-file no encontrado: {task_file}")
            return 2
        try:
            with open(task_file) as f:
                task_json = f.read()
        except Exception as e:
            eprint(f"❌ Error leyendo --task-file: {e}")
            return 2
    if not task_json:
        eprint("❌ --task o --task-file es requerido con un objeto JSON de la tarea")
        return 2
    try:
        new_task_data = json.loads(task_json)
    except json.JSONDecodeError as e:
        eprint(f"❌ --task JSON inválido: {e}")
        return 2

    if not isinstance(new_task_data, dict):
        eprint("❌ --task debe ser un objeto JSON (dict), no un array")
        return 2

    # Validate required fields for new task
    required_fields = ["objective", "type", "domain", "result_state"]
    missing = [f for f in required_fields if f not in new_task_data]
    if missing:
        eprint(f"❌ --task faltan campos requeridos: {', '.join(missing)}")
        eprint("   Requeridos: objective, type, domain, result_state")
        eprint(
            "   También recomendados: validation.predicates, impacted_artifacts.files, risks.curvature"
        )
        return 2

    # Validate content quality: objective >= 10 chars, result_state >= 10 chars
    obj_val = new_task_data.get("objective", "")
    rs_val = new_task_data.get("result_state", "")
    if len(obj_val) < 10:
        eprint(
            f"❌ --task.objective debe tener al menos 10 caracteres (actual: {len(obj_val)})"
        )
        eprint("   La descripcion de la tarea debe ser explicita y detallada.")
        return 2
    if len(rs_val) < 10:
        eprint(
            f"❌ --task.result_state debe tener al menos 10 caracteres (actual: {len(rs_val)})"
        )
        eprint("   La Definition of Done (DoD) debe describir el estado esperado.")
        return 2

    # Auto-generate task_id (o usar el del input si es valido y no duplicado)
    existing_ids = [t.get("task_id", "") for t in pl.get("tasks", [])]
    input_tid = new_task_data.get("task_id", "")
    if (
        input_tid
        and re.match(r"^TASK-\d+$", input_tid)
        and input_tid not in existing_ids
    ):
        new_task_id = input_tid
    else:
        max_num = 0
        for tid in existing_ids:
            m = re.search(r"TASK-(\d+)", tid)
            if m:
                max_num = max(max_num, int(m.group(1)))
        new_num = max_num + 1
        new_task_id = f"TASK-{new_num:03d}"

    scrum_role = new_task_data.get("scrum_role", flags.get("scrum-role", "po"))

    # Build the full task object
    new_task = {
        "task_id": new_task_id,
        "role": new_task_data.get("role", "core"),
        "scrum_role": scrum_role,
        "type": new_task_data.get("type", "feature"),
        "work_type": new_task_data.get("work_type", "refactor"),
        "objective": new_task_data["objective"],
        "result_state": new_task_data.get("result_state", new_task_data["objective"]),
        "domain": new_task_data["domain"],
        "validation": new_task_data.get("validation", {"predicates": []}),
        "impacted_artifacts": new_task_data.get("impacted_artifacts", {"files": []}),
        "risks": new_task_data.get("risks", {"curvature": 0.20}),
        "depends_on": new_task_data.get("depends_on", []),
        "preconditions": new_task_data.get("preconditions", []),
        "acceptance_criteria": new_task_data.get("acceptance_criteria", []),
        "quality_gates": new_task_data.get("quality_gates", []),
        "loop_verification": new_task_data.get("loop_verification", []),
        "loop_acceptance": new_task_data.get("loop_acceptance", "all"),
        "decisions": new_task_data.get("decisions", []),
        "refinement": new_task_data.get("refinement", {}),
        "importer_files": new_task_data.get("importer_files", 0),
    }
    # Copy any extra fields from input
    for k, v in new_task_data.items():
        if k not in new_task:
            new_task[k] = v

    # ── PRE-WRITE VALIDATION: Check against schema ──
    # Auto-generate task_context if missing (required by schema but not
    # always provided by the LLM in single-task additions)
    if "task_context" not in new_task:
        obj = new_task.get("objective", "")[:100]
        files = new_task.get("impacted_artifacts", {}).get("files", [])
        file_list = ", ".join(files) if files else "por determinar"
        domain = new_task.get("domain", "core")
        new_task["task_context"] = {
            "observation": f"Objetivo: {obj}. Archivos: {file_list}.",
            "structure": f"Archivos {file_list} en dominio {domain}.",
            "behavior": f"Implementar: {obj}.",
            "causality": "Restricciones a investigar.",
            "purpose": "Cumplir objetivo de la tarea.",
            "principles": f"Convenciones del proyecto en {domain}.",
        }

    # ── QUALITY GATE: acceptance_criteria, quality_gates, preconditions ──
    missing = []
    if not new_task.get("acceptance_criteria"):
        missing.append("acceptance_criteria: define criterios de aceptación concretos")
    if not new_task.get("quality_gates"):
        missing.append("quality_gates: define barreras de calidad específicas")
    if not new_task.get("preconditions"):
        missing.append("preconditions: define qué debe estar listo antes de empezar")
    if missing:
        eprint()
        eprint("=" * 70)
        eprint(
            "❌ QUALITY GATE: Tarea incompleta — el PO no puede validar sin estos datos"
        )
        eprint("=" * 70)
        for m in missing:
            eprint(f"  • {m}")
        eprint()
        eprint("  Incluye estos campos en el --task JSON:")
        eprint('  "acceptance_criteria": ["Criterio 1", "Criterio 2"],')
        eprint('  "quality_gates": ["Gate 1", "Gate 2"],')
        eprint('  "preconditions": ["Precondición 1", "Precondición 2"],')
        eprint()
        eprint("  Vuelve a ejecutar add-task con todos los campos completos.")
        eprint("=" * 70)
        return 2

    valid, field_errors = _validate_task_fields([new_task])
    if not valid:
        eprint()
        eprint("=" * 70)
        eprint("❌ VALIDACIÓN PRE-WRITE: Faltan campos requeridos en la tarea")
        eprint("=" * 70)
        for err in field_errors:
            eprint(err)
        eprint()
        eprint("  Referencia: schema/plan.schema.json → $defs/task → required")
        eprint()
        eprint("  Template de tarea completa:")
        eprint('  {"task_id": "...", "type": "feature",')
        eprint('   "objective": "Implementar X",')
        eprint('   "result_state": "X funciona correctamente",')
        eprint('   "domain": "core",')
        eprint('   "impacted_artifacts": {"files": ["ruta/archivo.py"]},')
        eprint('   "risks": {"curvature": 0.20, "commit_trust": 0.70},')
        eprint('   "task_context": {"observation": "...", ...},')
        eprint('   "validation": {"predicates": ["Una función X existe"]}}')
        eprint("=" * 70)
        return 2

    # Add task to plan
    pl.setdefault("tasks", []).append(new_task)

    # NOTA: Antes se agregaban predicates de la task a goal.must_exist
    # pero esto rompia el schema cuando los predicates eran dicts (TASK-025).
    # Los predicates de validacion pertenecen a la task, no al goal.

    # Update sprint-state.json
    task_states = prog.setdefault("current", {}).setdefault("task_states", {})
    task_states[new_task_id] = "pending"

    # Update next_action if there's no current next action
    # (or if the current next_action is already completed)
    current_next = prog.get("next_action", {})
    if not current_next or current_next.get("task_id") is None:
        prog["current"]["next_action"] = {
            "task_id": new_task_id,
            "type": new_task.get("type", "feature"),
            "work_type": new_task.get("work_type", "refactor"),
            "target": "",
            "delta": 0.0,
            "confidence": new_task.get("risks", {}).get("commit_trust", 0.70),
            "risk": new_task.get("risks", {}).get("curvature", 0.20),
            "score": 0.0,
            "candidate": new_task.get("objective", "")[:100],
            "rationale": f"Nueva tarea agregada: {new_task.get('objective', '')[:60]}...",
        }

    # Add trail entry
    trail = prog.get("audit", {}).get("trail", [])
    trail.append(
        {
            "at": _now_iso(),
            "distance": 0.0,
            "event": f"{new_task_id} added to sprint: {new_task.get('objective', '')[:80]}",
        }
    )

    # ── ENFORCEMENT: Write and validate ────────────────────────
    _ensure_v3_defaults(plan)
    try:
        write_json(plan_path, plan)
        write_json(sprint_state_path, progress)
    except RuntimeError:
        # write_json fallo por validacion de schema — rollback en memoria
        pl["tasks"] = [t for t in pl["tasks"] if t.get("task_id") != new_task_id]
        task_states.pop(new_task_id, None)
        return 2

    plan_valid = _validate_schema(plan_path, "plan.schema.json")
    prog_valid = _validate_schema(sprint_state_path, "sprint-state.schema.json")
    if not plan_valid or not prog_valid:
        eprint()
        eprint("=" * 70)
        eprint("❌ ENFORCEMENT BLOCKED: add-task produjo datos inválidos")
        eprint("=" * 70)
        eprint("  La tarea no fue agregada. Corrige los errores de validación")
        eprint("  arriba y reintenta.")
        eprint("  Si el error es del schema, verifica que el --task JSON tenga")
        eprint("  todos los campos requeridos con valores no vacíos.")
        eprint("=" * 70)
        # Rollback: remove the task we just added
        pl["tasks"] = [t for t in pl["tasks"] if t.get("task_id") != new_task_id]
        task_states.pop(new_task_id, None)
        # Rewrite original data
        try:
            write_json(plan_path, plan)
            write_json(sprint_state_path, progress)
        except RuntimeError:
            pass  # rollback tambien fallo, no podemos hacer mas
        return 2

    _kv_save(chain_id, "plan", plan, project_id)
    _kv_save(chain_id, "progress", progress, project_id)

    # ── Self-sufficient output ─────────────────────────────────
    print()
    print("=" * 70)
    print(f"✅ TASK ADDED: {new_task_id}")
    print("=" * 70)
    print(f"  Objective:   {new_task.get('objective', '')[:100]}")
    print(f"  Type:        {new_task.get('type', '?')}")
    print(f"  Domain:      {new_task.get('domain', '?')}")
    print(f"  Predicates:  {len(new_task.get('validation', {}).get('predicates', []))}")
    print(
        f"  Files:       {len(new_task.get('impacted_artifacts', {}).get('files', []))}"
    )
    print("  PO Approved: ❌ PENDIENTE — la tarea NO puede avanzar hasta aprobación")
    print(f"  Total tasks: {len(pl.get('tasks', []))}")
    print("  Schema:      ✅ plan + progress válidos")
    print()

    # Renderizar ticket PO completo
    _print_po_ticket(chain_dir, new_task_id, new_task)

    print()
    print("📋 ACCIÓN DEL PO:")
    print(f"  [1] ✅ APROBAR → spring.py approve-task {chain_dir} {new_task_id}")
    print(
        f"  [2] ❌ RECHAZAR → spring.py update-task {chain_dir} {new_task_id} --<campo> <valor>"
    )
    print()
    print(
        "  ⛔ La tarea NO puede avanzar (task.py advance) hasta que el PO la apruebe."
    )
    print("=" * 70)
    return 0


def _print_po_ticket(chain_dir: str, task_id: str, task: dict) -> None:
    """Renderiza un ticket PO con todos los campos de la tarea."""
    tc = task.get("task_context", {})
    r = task.get("risks", {})
    files = task.get("impacted_artifacts", {}).get("files", [])

    curv = r.get("curvature", "?")
    curv_label = (
        "muy baja"
        if (isinstance(curv, (int, float)) and curv <= 0.2)
        else "baja"
        if (isinstance(curv, (int, float)) and curv <= 0.3)
        else "moderada"
        if (isinstance(curv, (int, float)) and curv <= 0.4)
        else "alta"
    )
    trust = r.get("commit_trust", "?")
    trust_label = (
        "alta"
        if (isinstance(trust, (int, float)) and trust > 0.85)
        else "moderada"
        if (isinstance(trust, (int, float)) and trust > 0.7)
        else "baja"
    )

    print("═══════════════════════════════════════════════════════════════")
    print(f"  📋  TICKET PO — {task_id}")
    print("═══════════════════════════════════════════════════════════════")
    print(f"  🎯 {task.get('task_description', '')[:100]}")
    print(f"  📝 {task.get('objective', '')[:200]}")
    print(f"  📦 {task.get('result_state', '')[:200]}")
    print("═══════════════════════════════════════════════════════════════")
    print(
        f"  Tipo: {task.get('type', '?')} | Dominio: {task.get('domain', '?')} | Rol: {task.get('role', '?')}"
    )
    print(
        f"  Work: {task.get('work_type', '?')} | Scrum: {task.get('scrum_role', '?')}"
    )
    print(f"  Dependencias: {', '.join(task.get('depends_on', [])) or 'ninguna'}")
    print("═══════════════════════════════════════════════════════════════")
    print("  📋 PRECONDICIONES:")
    for p in task.get("preconditions", []):
        print(f"    🔹 {p[:100]}")
    print("  ✅ CRITERIOS DE ACEPTACIÓN:")
    for ac in task.get("acceptance_criteria", []):
        print(f"    ✅ {ac[:100]}")
    print("  🔒 QUALITY GATES:")
    for qg in task.get("quality_gates", []):
        print(f"    🔒 {qg[:100]}")
    print("═══════════════════════════════════════════════════════════════")
    print(f"  📄 Archivos ({len(files)}):")
    for f in files:
        print(f"    📄 {f}")
    print(
        f"  ⚠️  Riesgos: curvature={curv} ({curv_label}) | trust={trust} ({trust_label})"
    )
    print("═══════════════════════════════════════════════════════════════")
    print("  📓 CONTEXTO (6 capas):")
    for capa in [
        "observation",
        "structure",
        "behavior",
        "causality",
        "purpose",
        "principles",
    ]:
        val = tc.get(capa, "")
        if val:
            print(f"    {capa}: {val[:150]}")
    print("═══════════════════════════════════════════════════════════════")


def cmd_update_task(args: list[str]) -> int:
    """Update metadata of an existing task.

    Allowed fields: objective, result_state, domain, work_type, type,
    risks, impacted_artifacts, validation, depends_on, role,
    acceptance_criteria, quality_gates, task_context.

    Usage:
      spring.py update-task <chain_dir> <task_id> --objective "New text"
      spring.py update-task <chain_dir> <task_id> --domain "core" --work_type "refactor"
      spring.py update-task <chain_dir> <task_id> --task_context '{"observation":"..."}'
    """
    positional, flags = _parse_args(args)
    if len(positional) != 2:
        eprint(
            "❌ Uso: spring.py update-task <chain_dir> <task_id> [--field value ...]"
        )
        eprint(f"   Campos editables: {', '.join(sorted(_EDITABLE_TASK_FIELDS))}")
        return 2

    chain_dir = os.path.abspath(positional[0])
    task_id = positional[1]

    plan_path = os.path.join(chain_dir, "plan.json")
    sprint_state_path = os.path.join(chain_dir, "sprint-state.json")

    plan = load_json(plan_path)
    progress = load_json(sprint_state_path)
    if not plan or not progress:
        eprint(f"❌ Sprint no encontrado en {chain_dir}")
        return 2

    pl = plan.get("plan", plan)
    prog = progress  # sprint-state.json data
    chain_id = pl.get("meta", {}).get("chain_id", os.path.basename(chain_dir))
    project_id = pl.get("project", {}).get("project_id", "")

    # Sprint must be active
    status = prog.get("meta", {}).get("status", "unknown")
    if status == "closed":
        eprint(f"❌ Sprint '{chain_id}' está cerrado. No se pueden modificar tareas.")
        return 2

    # Find the task
    task = None
    for t in pl.get("tasks", []):
        if t.get("task_id") == task_id:
            task = t
            break
    if not task:
        eprint(f"❌ Tarea '{task_id}' no encontrada en plan.json")
        return 2

    # Si --task_context-file, leer el JSON desde archivo
    if "task_context-file" in flags:
        ctx_file = flags["task_context-file"]
        if not os.path.exists(ctx_file):
            eprint(f"❌ --task_context-file no encontrado: {ctx_file}")
            return 2
        try:
            with open(ctx_file) as f:
                ctx_data = json.load(f)
            flags["task_context"] = json.dumps(ctx_data)
            del flags["task_context-file"]  # limpia para no fallar validacion
        except Exception as e:
            eprint(f"❌ Error leyendo --task_context-file: {e}")
            return 2

    # Use already-parsed flags from _parse_args
    updates = {k: v for k, v in flags.items() if k not in ("task",)}

    if not updates:
        eprint("⚠️  No se especificaron campos para actualizar.")
        eprint(f"   Campos editables: {', '.join(sorted(_EDITABLE_TASK_FIELDS))}")
        return 0

    changes = []
    for field, value in updates.items():
        if field not in _EDITABLE_TASK_FIELDS:
            eprint(f"❌ Campo no editable: {field}")
            eprint(f"   Campos editables: {', '.join(sorted(_EDITABLE_TASK_FIELDS))}")
            return 2
        # Try to parse JSON values (for dicts, arrays, numbers)
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            parsed = value  # Keep as string
        task.get(field)
        task[field] = parsed
        changes.append(field)

    # ── ENFORCEMENT: Validate only the updated task, not the full plan ──
    # Las tareas creadas antes de v3 pueden no tener todos los campos requeridos.
    # Poblamos los faltantes con defaults antes de validar.
    _ensure_v3_defaults(plan)

    write_json(plan_path, plan)

    task_schema_path = os.path.join(_BASE_DIR, "schema", "plan.schema.json")
    task_valid = True
    if os.path.exists(task_schema_path):
        try:
            with open(task_schema_path) as f:
                full_schema = json.load(f)
            task_def = full_schema.get("$defs", {}).get("task", {})
            if task_def:
                # Write task + task schema to temp files for validation
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", delete=False
                ) as tf:
                    json.dump(task, tf, indent=2, ensure_ascii=False)
                    task_tmp = tf.name
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", delete=False
                ) as sf:
                    json.dump(task_def, sf, indent=2, ensure_ascii=False)
                    schema_tmp = sf.name
                r = subprocess.run(
                    [
                        sys.executable,
                        os.path.join(_SCRIPT_DIR, "validate-schema.py"),
                        task_tmp,
                        schema_tmp,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if r.returncode != 0:
                    task_valid = False
                    if r.stdout:
                        for line in r.stdout.splitlines():
                            if any(
                                tag in line
                                for tag in (
                                    "❌",
                                    "Mensaje:",
                                    "Error",
                                    "#1",
                                    "#2",
                                    "RECOVERY",
                                )
                            ):
                                eprint(f"  {line.strip()}")
                os.unlink(task_tmp)
                os.unlink(schema_tmp)
        except Exception:
            pass

    if not task_valid:
        eprint()
        eprint("=" * 70)
        eprint("❌ ENFORCEMENT BLOCKED: update-task produjo tarea inválida")
        eprint("=" * 70)
        eprint("  Los cambios no se guardaron. Revierte los campos y reintenta.")
        eprint(f"  Tarea: {task_id}")
        eprint(f"  Campos modificados: {', '.join(changes)}")
        eprint("=" * 70)
        return 2

    # Add trail entry
    trail = prog.get("audit", {}).get("trail", [])
    trail.append(
        {
            "at": _now_iso(),
            "distance": 0.0,
            "event": f"{task_id} updated: {', '.join(changes)}",
        }
    )

    write_json(sprint_state_path, progress)
    _kv_save(chain_id, "plan", plan, project_id)
    _kv_save(chain_id, "progress", progress, project_id)

    # ── Self-sufficient output ─────────────────────────────────
    print()
    print("=" * 70)
    print(f"✅ TASK UPDATED: {task_id}")
    print("=" * 70)
    print(f"  Campos modificados: {', '.join(changes)}")
    print("  Schema:            ✅ plan.json válido")
    print()
    print("📋 DECISION PROTOCOL:")
    print()
    print("  ▶️  Para continuar con la tarea:")
    print(f"     python3 scripts/task.py advance {chain_dir} {task_id}")
    print()
    print("  ▶️  Para ver estado:")
    print(f"     python3 scripts/spring.py status {chain_dir}")
    print("=" * 70)
    return 0


def cmd_update_goal(args: list[str]) -> int:
    """Update the sprint goal description and/or predicates.

    Refines the G_objetivo without changing existing tasks.

    Usage:
      spring.py update-goal <chain_dir> --goal "new description"
      spring.py update-goal <chain_dir> --predicates '{"must_exist":["P1"],...}'
      spring.py update-goal <chain_dir> --goal "..." --predicates '{}'
    """
    positional, flags = _parse_args(args)
    if len(positional) != 1:
        eprint(
            "❌ Uso: spring.py update-goal <chain_dir> [--goal '...'] [--predicates '{}']"
        )
        eprint("   Al menos uno de --goal o --predicates es requerido.")
        return 2

    chain_dir = os.path.abspath(positional[0])
    plan_path = os.path.join(chain_dir, "plan.json")
    sprint_state_path = os.path.join(chain_dir, "sprint-state.json")

    plan = load_json(plan_path)
    progress = load_json(sprint_state_path)
    if not plan or not progress:
        eprint(f"❌ Sprint no encontrado en {chain_dir}")
        return 2

    pl = plan.get("plan", plan)
    prog = progress  # sprint-state.json data
    chain_id = pl.get("meta", {}).get("chain_id", os.path.basename(chain_dir))
    project_id = pl.get("project", {}).get("project_id", "")

    # Sprint must be active
    status = prog.get("meta", {}).get("status", "unknown")
    if status == "closed":
        eprint(f"❌ Sprint '{chain_id}' está cerrado. No se puede modificar el goal.")
        return 2

    new_goal = flags.get("goal", "")
    new_predicates_json = flags.get("predicates", "")

    if not new_goal and not new_predicates_json:
        eprint("❌ Al menos uno de --goal o --predicates es requerido")
        return 2

    g_obj = pl.setdefault("goal", {})
    changes = []

    if new_goal:
        if len(new_goal) < 10:
            eprint("❌ --goal debe tener al menos 10 caracteres")
            return 2
        g_obj["description"] = new_goal
        changes.append("description")

    if new_predicates_json:
        try:
            new_preds = json.loads(new_predicates_json)
        except json.JSONDecodeError as e:
            eprint(f"❌ --predicates JSON inválido: {e}")
            return 2
        if not isinstance(new_preds, dict):
            eprint("❌ --predicates debe ser un objeto JSON")
            return 2
        # Merge predicates (don't lose existing ones)
        existing = g_obj.setdefault(
            "predicates", {"must_exist": [], "must_not_exist": [], "invariants": []}
        )
        for ptype in ("must_exist", "must_not_exist", "invariants"):
            incoming = new_preds.get(ptype, [])
            existing_list = existing.setdefault(ptype, [])
            for p in incoming:
                if p not in existing_list:
                    existing_list.append(p)
        changes.append("predicates")

    if not changes:
        eprint("⚠️  No se realizaron cambios.")
        return 0

    # ── ENFORCEMENT: Write and validate ────────────────────────
    write_json(plan_path, plan)

    plan_valid = _validate_schema(plan_path, "plan.schema.json")
    if not plan_valid:
        eprint()
        eprint("=" * 70)
        eprint("❌ ENFORCEMENT BLOCKED: update-goal produjo plan.json inválido")
        eprint("=" * 70)
        eprint("  Los cambios no se guardaron. Corrige los errores y reintenta.")
        eprint("=" * 70)
        return 2

    # Add trail entry
    trail = prog.get("audit", {}).get("trail", [])
    old_desc = g_obj.get("description", "")[:60]
    trail.append(
        {
            "at": _now_iso(),
            "distance": 0.0,
            "event": f"Goal refined: {new_goal[:80] if new_goal else old_desc}",
        }
    )
    write_json(sprint_state_path, progress)

    _kv_save(chain_id, "plan", plan, project_id)
    _kv_save(chain_id, "progress", progress, project_id)

    # ── Self-sufficient output ─────────────────────────────────
    print()
    print("=" * 70)
    print(f"✅ GOAL UPDATED: {chain_id}")
    print("=" * 70)
    if new_goal:
        print(f"  Goal:        {new_goal[:100]}")
    if new_predicates_json:
        preds = g_obj.get("predicates", {})
        me = len(preds.get("must_exist", []))
        mn = len(preds.get("must_not_exist", []))
        inv = len(preds.get("invariants", []))
        print(f"  Predicates:  {me} must_exist, {mn} must_not_exist, {inv} invariants")
    print("  Schema:      ✅ plan.json válido")
    print()
    print("📋 DECISION PROTOCOL:")
    print()
    print("  ▶️  Las tareas existentes no fueron modificadas.")
    print("  ▶️  Para ver estado actualizado:")
    print(f"     python3 scripts/spring.py status {chain_dir}")
    print()
    print("  ▶️  Si el alcance cambió significativamente, considera:")
    print(f"     python3 scripts/spring.py add-task {chain_dir} --task '...'")
    print("=" * 70)
    return 0


def cmd_bulk_update(args: list[str]) -> int:
    """Actualiza múltiples tasks en una operación (TASK-028).

    Uso: spring.py bulk-update <chain_dir> --set <campo=valor> [--filter <k=v>]

    Ejemplos:
      spring.py bulk-update <chain> --set validation.predicates=[] --filter status=pending
      spring.py bulk-update <chain> --set risks.curvature=0.2 --filter type=feature
    """
    if len(args) < 1:
        eprint(
            "Uso: spring.py bulk-update <chain_dir> --set field=value [--filter k=v]"
        )
        return 2

    chain_dir = os.path.abspath(args[0])
    plan_path = os.path.join(chain_dir, "plan.json")
    plan = load_json(plan_path)
    if not plan:
        return 2

    pl = plan.get("plan", plan)
    tasks = pl.get("tasks", [])
    if not tasks:
        eprint("(sin tasks)")
        return 0

    # Parse --set and --filter
    set_field = set_value = None
    filter_field = filter_value = None

    for i, a in enumerate(args):
        if a == "--set" and i + 1 < len(args):
            parts = args[i + 1].split("=", 1)
            if len(parts) == 2:
                set_field, set_value = parts
        if a == "--filter" and i + 1 < len(args):
            parts = args[i + 1].split("=", 1)
            if len(parts) == 2:
                filter_field, filter_value = parts

    if not set_field:
        eprint("❌ Requiere --set field=value")
        return 2

    updated = 0
    for t in tasks:
        # Apply filter
        if filter_field:
            actual = _get_nested(t, filter_field)
            if str(actual) != filter_value:
                continue

        # Parse value: try JSON first, fallback to string
        try:
            parsed = json.loads(set_value)
        except (json.JSONDecodeError, TypeError):
            parsed = set_value

        _set_nested(t, set_field, parsed)
        updated += 1

    if updated == 0:
        print("(0 tasks actualizadas)")
        return 0

    write_json(plan_path, plan)
    print(
        f"✅ {updated} tasks actualizadas ({'filtro: ' + filter_field + '=' + filter_value if filter_field else 'todas'})"
    )
    return 0


# ═══════════════════════════════════════════════════════════════
# TASK-010: cmd_split_task
# ═══════════════════════════════════════════════════════════════


def cmd_split_task(args: list[str]) -> int:
    """Divide una tarea en N subtareas (TASK-010).

    Crea N-1 nuevas tareas a partir de la original, redistribuyendo
    el scope. La tarea original se conserva como la primera subtarea
    con el mismo task_id y su objective actualizado.

    Usage:
      spring.py split-task <chain_dir> <task_id> --parts 3
      spring.py split-task <chain_dir> <task_id> --parts 2 --objectives '["Parte 1","Parte 2"]'
    """
    positional, flags = _parse_args(args)
    if len(positional) != 2:
        eprint("❌ Uso: spring.py split-task <chain_dir> <task_id> --parts N")
        eprint('   [--objectives \'["Obj1","Obj2",...]\']  Objetivos personalizados')
        eprint(
            "   [--result_states '[\"RS1\",...]']         Result states personalizados"
        )
        return 2

    chain_dir = os.path.abspath(positional[0])
    task_id = positional[1]

    plan_path = os.path.join(chain_dir, "plan.json")
    sprint_state_path = os.path.join(chain_dir, "sprint-state.json")

    plan = load_json(plan_path)
    progress = load_json(sprint_state_path)
    if not plan or not progress:
        eprint(f"❌ Sprint no encontrado en {chain_dir}")
        return 2

    pl = plan.get("plan", plan)
    prog = progress
    chain_id = pl.get("meta", {}).get("chain_id", os.path.basename(chain_dir))
    project_id = pl.get("project", {}).get("project_id", "")

    # Sprint must be active
    status = prog.get("meta", {}).get("status", "unknown")
    if status == "closed":
        eprint(f"❌ Sprint '{chain_id}' está cerrado. No se pueden dividir tareas.")
        return 2

    # Find the task
    tasks = pl.get("tasks", [])
    task = None
    for t in tasks:
        if t.get("task_id") == task_id:
            task = t
            break
    if not task:
        eprint(f"❌ Tarea '{task_id}' no encontrada en plan.json")
        return 2

    # Parse --parts (required)
    try:
        parts = int(flags.get("parts", "0"))
    except ValueError:
        parts = 0
    if parts < 2:
        eprint("❌ --parts debe ser un entero >= 2")
        return 2

    # Parse --objectives (optional)
    objectives_json = flags.get("objectives", "")
    custom_objectives = []
    if objectives_json:
        try:
            parsed = json.loads(objectives_json)
            if isinstance(parsed, list) and len(parsed) == parts:
                custom_objectives = parsed
            else:
                eprint(f"❌ --objectives debe ser un array de {parts} strings")
                return 2
        except json.JSONDecodeError as e:
            eprint(f"❌ --objectives JSON inválido: {e}")
            return 2

    # Parse --result_states (optional)
    rs_json = flags.get("result_states", "")
    custom_rs = []
    if rs_json:
        try:
            parsed = json.loads(rs_json)
            if isinstance(parsed, list) and len(parsed) == parts:
                custom_rs = parsed
        except json.JSONDecodeError as e:
            eprint(f"❌ --result_states JSON inválido: {e}")
            return 2

    # Auto-generate new task IDs
    existing_ids = [t.get("task_id", "") for t in tasks]
    max_num = 0
    for tid in existing_ids:
        m = re.search(r"TASK-(\d+)", tid)
        if m:
            max_num = max(max_num, int(m.group(1)))

    # Build subtasks
    new_tasks = []
    for i in range(parts):
        max_num += 1
        new_tid = f"TASK-{max_num:03d}"

        if custom_objectives:
            obj = custom_objectives[i]
        else:
            obj = f"{task.get('objective', '')} (parte {i + 1}/{parts})"

        rs = (
            custom_rs[i]
            if custom_rs
            else f"{task.get('result_state', '')} (parte {i + 1}/{parts})"
        )

        # Copy original task fields but override objective/result_state/task_id
        subtask = dict(task)
        subtask["task_id"] = new_tid
        subtask["objective"] = obj
        subtask["result_state"] = rs
        subtask["depends_on"] = [task_id] if i == 0 else [f"TASK-{max_num - 1:03d}"]
        subtask["preconditions"] = []
        subtask["acceptance_criteria"] = [
            f"{c} (parte {i + 1})" for c in task.get("acceptance_criteria", [])
        ]
        new_tasks.append(subtask)

    # Replace original task with first subtask + append rest
    task["task_id"]  # Keep original ID for first subtask
    first_obj = custom_objectives[0] if custom_objectives else task.get("objective", "")
    first_rs = custom_rs[0] if custom_rs else task.get("result_state", "")
    task["objective"] = f"{first_obj} [split {parts}]"
    task["result_state"] = first_rs
    task["depends_on"] = task.get("depends_on", [])  # Preserve original dependencies

    # Insert new tasks after the original
    insert_idx = tasks.index(task) + 1
    for i, st in enumerate(new_tasks):
        tasks.insert(insert_idx + i, st)

    # Update sprint-state.json task_states
    task_states = prog.setdefault("current", {}).setdefault("task_states", {})
    for st in new_tasks:
        task_states[st["task_id"]] = "pending"

    # Add trail entry
    trail = prog.get("audit", {}).get("trail", [])
    trail.append(
        {
            "at": _now_iso(),
            "distance": 0.0,
            "event": f"{task_id} split into {parts} tasks: {', '.join(st['task_id'] for st in new_tasks)}",
        }
    )

    # ── ENFORCEMENT: Write and validate ────────────────────────
    _ensure_v3_defaults(plan)
    write_json(plan_path, plan)
    write_json(sprint_state_path, progress)

    plan_valid = _validate_schema(plan_path, "plan.schema.json")
    prog_valid = _validate_schema(sprint_state_path, "sprint-state.schema.json")
    if not plan_valid or not prog_valid:
        eprint()
        eprint("=" * 70)
        eprint("❌ ENFORCEMENT BLOCKED: split-task produjo datos inválidos")
        eprint("=" * 70)
        eprint("  Los cambios no se guardaron. Corrige los errores y reintenta.")
        eprint("=" * 70)
        return 2

    _kv_save(chain_id, "plan", plan, project_id)
    _kv_save(chain_id, "progress", progress, project_id)

    # ── Output ─────────────────────────────────────────────────
    print()
    print("=" * 70)
    print(f"✅ TASK SPLIT: {task_id} → {parts} subtareas")
    print("=" * 70)
    print(f"  Original: {task_id} (conservada, objective ajustado)")
    for i, st in enumerate(new_tasks):
        print(f"  {i + 1}. {st['task_id']}: {st.get('objective', '')[:80]}")
    print(f"  Total tasks: {len(pl.get('tasks', []))}")
    print("  Schema:      ✅ plan + progress válidos")
    print("=" * 70)
    return 0


# ═══════════════════════════════════════════════════════════════
# TASK-011: cmd_batch_add
# ═══════════════════════════════════════════════════════════════


def cmd_batch_add(args: list[str]) -> int:
    """Add multiple tasks at once (TASK-011).

    Acepta un array JSON de tareas via --tasks o --tasks-file.
    Cada tarea sigue el mismo formato que add-task pero sin task_id
    (se auto-generan secuencialmente).

    Usage:
      spring.py batch-add <chain_dir> --tasks '[{"objective":"...","type":"feature",...}]'
      spring.py batch-add <chain_dir> --tasks-file ./tasks.json
    """
    positional, flags = _parse_args(args)
    if len(positional) != 1:
        eprint("❌ Uso: spring.py batch-add <chain_dir> --tasks '[...]'")
        eprint("   --tasks-file <path>   Alternativa: leer --tasks desde archivo JSON")
        return 2

    chain_dir = os.path.abspath(positional[0])
    plan_path = os.path.join(chain_dir, "plan.json")
    sprint_state_path = os.path.join(chain_dir, "sprint-state.json")

    plan = load_json(plan_path)
    progress = load_json(sprint_state_path)
    if not plan or not progress:
        eprint(f"❌ Sprint no encontrado en {chain_dir}")
        return 2

    pl = plan.get("plan", plan)
    prog = progress
    chain_id = pl.get("meta", {}).get("chain_id", os.path.basename(chain_dir))
    project_id = pl.get("project", {}).get("project_id", "")

    # Sprint must be active
    status = prog.get("meta", {}).get("status", "unknown")
    if status == "closed":
        eprint(f"❌ Sprint '{chain_id}' ya está cerrado. No se pueden agregar tareas.")
        return 2

    # Parse --tasks JSON (or --tasks-file)
    tasks_json = flags.get("tasks", "")
    tasks_file = flags.get("tasks-file", "")
    if tasks_file:
        if not os.path.exists(tasks_file):
            eprint(f"❌ --tasks-file no encontrado: {tasks_file}")
            return 2
        try:
            with open(tasks_file) as f:
                tasks_json = f.read()
        except Exception as e:
            eprint(f"❌ Error leyendo --tasks-file: {e}")
            return 2

    if not tasks_json:
        eprint("❌ --tasks o --tasks-file es requerido con un array JSON de tareas")
        return 2

    try:
        new_tasks_data = json.loads(tasks_json)
    except json.JSONDecodeError as e:
        eprint(f"❌ --tasks JSON inválido: {e}")
        return 2

    if not isinstance(new_tasks_data, list) or len(new_tasks_data) == 0:
        eprint("❌ --tasks debe ser un array JSON no vacío")
        return 2

    # Validate required fields for all tasks
    required_fields = ["objective", "type", "domain", "result_state"]
    errors = []
    for i, t in enumerate(new_tasks_data):
        tid = t.get("task_id", f"#{i + 1}")
        missing = [f for f in required_fields if f not in t]
        if missing:
            errors.append(f"  ❌ Tarea {tid}: faltan campos: {', '.join(missing)}")
    if errors:
        eprint()
        eprint("=" * 70)
        eprint("❌ Error de validación en los datos de entrada")
        eprint("=" * 70)
        for e in errors:
            eprint(e)
        eprint("=" * 70)
        return 2

    # Auto-generate task IDs
    existing_ids = [t.get("task_id", "") for t in pl.get("tasks", [])]
    max_num = 0
    for tid in existing_ids:
        m = re.search(r"TASK-(\d+)", tid)
        if m:
            max_num = max(max_num, int(m.group(1)))

    scrum_role_default = flags.get("scrum-role", "po")

    added_tasks = []
    for td in new_tasks_data:
        max_num += 1
        new_task_id = f"TASK-{max_num:03d}"

        new_task = {
            "task_id": new_task_id,
            "role": td.get("role", "core"),
            "scrum_role": td.get("scrum_role", scrum_role_default),
            "type": td.get("type", "feature"),
            "work_type": td.get("work_type", "refactor"),
            "objective": td["objective"],
            "result_state": td.get("result_state", td["objective"]),
            "domain": td["domain"],
            "validation": td.get("validation", {"predicates": []}),
            "impacted_artifacts": td.get("impacted_artifacts", {"files": []}),
            "risks": td.get("risks", {"curvature": 0.20}),
            "depends_on": td.get("depends_on", []),
            "preconditions": td.get("preconditions", []),
            "acceptance_criteria": td.get("acceptance_criteria", []),
            "quality_gates": td.get("quality_gates", []),
            "loop_verification": td.get("loop_verification", []),
            "loop_acceptance": td.get("loop_acceptance", "all"),
            "decisions": td.get("decisions", []),
            "refinement": td.get("refinement", {}),
            "importer_files": td.get("importer_files", 0),
        }
        # Copy extra fields
        for k, v in td.items():
            if k not in new_task and k != "task_id":
                new_task[k] = v

        # Auto-generate task_context if missing
        if "task_context" not in new_task:
            obj = new_task.get("objective", "")[:100]
            files = new_task.get("impacted_artifacts", {}).get("files", [])
            file_list = ", ".join(files) if files else "por determinar"
            domain = new_task.get("domain", "core")
            new_task["task_context"] = {
                "observation": f"Objetivo: {obj}. Archivos: {file_list}.",
                "structure": f"Archivos {file_list} en dominio {domain}.",
                "behavior": f"Implementar: {obj}.",
                "causality": "Restricciones a investigar.",
                "purpose": "Cumplir objetivo de la tarea.",
                "principles": f"Convenciones del proyecto en {domain}.",
            }

        pl.setdefault("tasks", []).append(new_task)
        added_tasks.append(new_task)

    # Validate all tasks before writing
    valid, field_errors = _validate_task_fields(added_tasks)
    if not valid:
        eprint()
        eprint("=" * 70)
        eprint("❌ VALIDATION FAILED: Algunas tareas no pasaron validación")
        eprint("=" * 70)
        for err in field_errors:
            eprint(err)
        eprint("=" * 70)
        return 2

    # Update sprint-state.json
    task_states = prog.setdefault("current", {}).setdefault("task_states", {})
    trail = prog.get("audit", {}).get("trail", [])

    for t in added_tasks:
        task_states[t["task_id"]] = "pending"

    trail.append(
        {
            "at": _now_iso(),
            "distance": 0.0,
            "event": f"Batch add: {len(added_tasks)} tasks ({', '.join(t['task_id'] for t in added_tasks)})",
        }
    )

    # ── ENFORCEMENT: Write and validate ────────────────────────
    _ensure_v3_defaults(plan)
    write_json(plan_path, plan)
    write_json(sprint_state_path, progress)

    plan_valid = _validate_schema(plan_path, "plan.schema.json")
    prog_valid = _validate_schema(sprint_state_path, "sprint-state.schema.json")
    if not plan_valid or not prog_valid:
        eprint()
        eprint("=" * 70)
        eprint("❌ ENFORCEMENT BLOCKED: batch-add produjo datos inválidos")
        eprint("=" * 70)
        eprint("  Los cambios no se guardaron. Corrige los errores y reintenta.")
        eprint("=" * 70)
        return 2

    _kv_save(chain_id, "plan", plan, project_id)
    _kv_save(chain_id, "progress", progress, project_id)

    # ── Output ─────────────────────────────────────────────────
    print()
    print("=" * 70)
    print(f"✅ BATCH ADD: {len(added_tasks)} tareas agregadas")
    print("=" * 70)
    for t in added_tasks:
        print(f"  · {t['task_id']}: {t.get('objective', '')[:80]}")
    print(f"  Total tasks: {len(pl.get('tasks', []))}")
    print("  Schema:      ✅ plan + progress válidos")
    print("=" * 70)
    return 0


# ═══════════════════════════════════════════════════════════════
# TASK-012: cmd_reorder
# ═══════════════════════════════════════════════════════════════


def cmd_reorder(args: list[str]) -> int:
    """Reordena una tarea en el plan (TASK-012).

    Mueve una tarea a una posición específica dentro de la lista
    de tareas del plan.

    Usage:
      spring.py reorder <chain_dir> <task_id> --position N
      spring.py reorder <chain_dir> <task_id> --before <other_task_id>
      spring.py reorder <chain_dir> <task_id> --after <other_task_id>
    """
    positional, flags = _parse_args(args)
    if len(positional) != 2:
        eprint("❌ Uso: spring.py reorder <chain_dir> <task_id> --position N")
        eprint("       spring.py reorder <chain_dir> <task_id> --before <other_id>")
        eprint("       spring.py reorder <chain_dir> <task_id> --after <other_id>")
        return 2

    chain_dir = os.path.abspath(positional[0])
    task_id = positional[1]

    plan_path = os.path.join(chain_dir, "plan.json")
    plan = load_json(plan_path)
    if not plan:
        eprint(f"❌ Sprint no encontrado en {chain_dir}")
        return 2

    pl = plan.get("plan", plan)
    tasks = pl.get("tasks", [])
    if not tasks:
        eprint("(sin tasks)")
        return 0

    # Find the task
    task = None
    for t in tasks:
        if t.get("task_id") == task_id:
            task = t
            break
    if not task:
        eprint(f"❌ Tarea '{task_id}' no encontrada en plan.json")
        return 2

    current_idx = tasks.index(task)

    # Determine target position
    position_str = flags.get("position", "")
    before_id = flags.get("before", "")
    after_id = flags.get("after", "")

    if position_str:
        try:
            target_idx = int(position_str)
        except ValueError:
            eprint(f"❌ --position debe ser un entero (0-{len(tasks) - 1})")
            return 2
        if target_idx < 0 or target_idx >= len(tasks):
            eprint(f"❌ --position fuera de rango (0-{len(tasks) - 1})")
            return 2
    elif before_id:
        before_task = None
        for t in tasks:
            if t.get("task_id") == before_id:
                before_task = t
                break
        if not before_task:
            eprint(f"❌ Tarea '{before_id}' no encontrada para --before")
            return 2
        target_idx = tasks.index(before_task)
    elif after_id:
        after_task = None
        for t in tasks:
            if t.get("task_id") == after_id:
                after_task = t
                break
        if not after_task:
            eprint(f"❌ Tarea '{after_id}' no encontrada para --after")
            return 2
        target_idx = tasks.index(after_task) + 1
    else:
        eprint("❌ Requiere --position N, --before <id>, o --after <id>")
        return 2

    if target_idx == current_idx or target_idx == current_idx + 1:
        print("(sin cambios — la tarea ya está en esa posición)")
        return 0

    # Remove and re-insert
    tasks.remove(task)
    insert_idx = target_idx if target_idx < current_idx else target_idx - 1
    tasks.insert(insert_idx, task)

    # Add trail entry
    sprint_state_path = os.path.join(chain_dir, "sprint-state.json")
    progress = load_json(sprint_state_path)
    if progress:
        prog = progress
        trail = prog.get("audit", {}).get("trail", [])
        trail.append(
            {
                "at": _now_iso(),
                "distance": 0.0,
                "event": f"{task_id} reordered to position {insert_idx}",
            }
        )
        write_json(sprint_state_path, progress)

    write_json(plan_path, plan)

    print()
    print("=" * 70)
    print(f"✅ REORDERED: {task_id} → position {insert_idx}")
    print("=" * 70)
    print(f"  Task: {task.get('objective', '')[:80]}")
    print(f"  Total tasks: {len(tasks)}")
    print("=" * 70)
    return 0


# ═══════════════════════════════════════════════════════════════
# TASK-013: cmd_remove
# ═══════════════════════════════════════════════════════════════


def cmd_remove(args: list[str]) -> int:
    """Remove a task from the plan (TASK-013).

    Usage:
      spring.py remove <chain_dir> <task_id> [--reason "..."] [--force]
    """
    positional, flags = _parse_args(args)
    if len(positional) != 2:
        eprint(
            "❌ Uso: spring.py remove <chain_dir> <task_id> [--reason '...'] [--force]"
        )
        eprint("   --reason   Motivo de la eliminación")
        eprint("   --force    Eliminar incluso si la tarea no está en estado pending")
        return 2

    chain_dir = os.path.abspath(positional[0])
    task_id = positional[1]

    plan_path = os.path.join(chain_dir, "plan.json")
    sprint_state_path = os.path.join(chain_dir, "sprint-state.json")

    plan = load_json(plan_path)
    progress = load_json(sprint_state_path)
    if not plan or not progress:
        eprint(f"❌ Sprint no encontrado en {chain_dir}")
        return 2

    pl = plan.get("plan", plan)
    prog = progress
    pl.get("meta", {}).get("chain_id", os.path.basename(chain_dir))

    # Find the task
    tasks = pl.get("tasks", [])
    task = None
    for t in tasks:
        if t.get("task_id") == task_id:
            task = t
            break
    if not task:
        eprint(f"❌ Tarea '{task_id}' no encontrada en plan.json")
        return 2

    # Check status (unless --force)
    if "force" not in flags:
        task_states = prog.get("current", {}).get("task_states", {})
        status = task_states.get(task_id, "pending")
        if status not in ("pending", "cancelled"):
            eprint(
                f"❌ Tarea '{task_id}' está en estado '{status}'. Usa --force para eliminarla."
            )
            return 2

    reason = flags.get("reason", "")
    task_desc = task.get("objective", "")[:80]
    task_id = task.get("task_id", task_id)

    # ── Cleanup: eliminar referencias a esta tarea en otras tareas ──
    affected_tasks = []
    for other in pl.get("tasks", []):
        if other.get("task_id") == task_id:
            continue
        changed = False
        # Limpiar depends_on
        deps = other.get("depends_on", [])
        if task_id in deps:
            other["depends_on"] = [d for d in deps if d != task_id]
            changed = True
        # Limpiar preconditions
        precs = other.get("preconditions", [])
        new_precs = []
        for p in precs:
            ref_task = p.split()[0] if p.split() else ""
            if ref_task == task_id:
                changed = True
            else:
                new_precs.append(p)
        if new_precs != precs:
            other["preconditions"] = new_precs
        if changed:
            affected_tasks.append(other.get("task_id", "?"))

    # Remove from plan
    pl["tasks"] = [t for t in tasks if t.get("task_id") != task_id]

    # Remove from sprint-state
    task_states = prog.setdefault("current", {}).setdefault("task_states", {})
    task_states.pop(task_id, None)

    # Add trail
    trail = prog.get("audit", {}).get("trail", [])
    event = f"{task_id} removed: {task_desc}"
    if affected_tasks:
        event += f" (referencias limpiadas en: {', '.join(affected_tasks)})"
    if reason:
        event += f" — {reason}"
    trail.append(
        {
            "at": _now_iso(),
            "distance": 0.0,
            "event": event,
        }
    )

    # ── ENFORCEMENT: Write and validate ────────────────────────
    _ensure_v3_defaults(plan)
    write_json(plan_path, plan)
    write_json(sprint_state_path, progress)

    plan_valid = _validate_schema(plan_path, "plan.schema.json")
    prog_valid = _validate_schema(sprint_state_path, "sprint-state.schema.json")
    if not plan_valid or not prog_valid:
        eprint()
        eprint("=" * 70)
        eprint("❌ ENFORCEMENT BLOCKED: remove produjo datos inválidos")
        eprint("=" * 70)
        eprint("  Los cambios no se guardaron.")
        eprint("=" * 70)
        return 2

    print()
    print("=" * 70)
    print(f"✅ TASK REMOVED: {task_id}")
    print("=" * 70)
    print(f"  Objective: {task_desc}")
    if reason:
        print(f"  Reason:    {reason}")
    print(f"  Total tasks: {len(pl.get('tasks', []))}")
    print("  Schema:      ✅ plan + progress válidos")
    print("=" * 70)
    return 0


# ═══════════════════════════════════════════════════════════════
# TASK-003-gap: cmd_validate_plan
# ═══════════════════════════════════════════════════════════════


def cmd_validate_plan(args: list[str]) -> int:
    """Valida la integridad del plan: dependencias y precondiciones.

    Verifica:
      1. depends_on refiera solo a tareas existentes
      2. preconditions refiera solo a tareas existentes
      3. Sin gaps numericos (TASK-001, TASK-002, TASK-004 → warning)

    Usage:
      spring.py validate-plan <chain_dir>

    Exit codes:
      0 — plan valido
      1 — plan tiene errores de dependencias
      2 — error
    """
    if len(args) < 1:
        eprint("❌ Uso: spring.py validate-plan <chain_dir>")
        return 2

    chain_dir = os.path.abspath(args[0])
    plan_path = os.path.join(chain_dir, "plan.json")
    plan = load_json(plan_path)
    if not plan:
        eprint(f"❌ Plan no encontrado en {chain_dir}")
        return 2

    pl = plan.get("plan", plan)
    tasks = pl.get("tasks", [])
    if not tasks:
        eprint("⚠️  Plan sin tareas — nada que validar.")
        return 0

    all_ids = {t.get("task_id") for t in tasks if t.get("task_id")}
    errors = []
    warnings = []

    # 1. Validar depends_on
    for t in tasks:
        tid = t.get("task_id", "?")
        for dep in t.get("depends_on", []):
            if dep not in all_ids:
                errors.append(f"  ❌ {tid}: depends_on '{dep}' — tarea no existe")

    # 2. Validar preconditions (primer token es task_id)
    for t in tasks:
        tid = t.get("task_id", "?")
        for prec in t.get("preconditions", []):
            ref_task = prec.split()[0] if prec.split() else ""
            if ref_task and ref_task.startswith("TASK-") and ref_task not in all_ids:
                errors.append(
                    f"  ❌ {tid}: precondition '{prec[:60]}...' refiere a '{ref_task}' — tarea no existe"
                )

    # 3. Detectar gaps numericos (warning)
    nums = []
    for tid in all_ids:
        m = re.search(r"TASK-(\d+)", tid)
        if m:
            nums.append(int(m.group(1)))
    if nums:
        nums.sort()
        expected = set(range(nums[0], nums[-1] + 1))
        actual = set(nums)
        missing = sorted(expected - actual)
        if missing:
            labels = [f"TASK-{n:03d}" for n in missing]
            warnings.append(f"  ⚠️  Gap numerico: faltan {', '.join(labels)}")

    # ── Output ─────────────────────────────────────────────────
    has_errors = len(errors) > 0
    has_warnings = len(warnings) > 0

    print()
    print("=" * 70)
    if has_errors:
        print(f"❌ VALIDATE PLAN: {len(errors)} error(es), {len(warnings)} warning(s)")
    elif has_warnings:
        print(f"⚠️  VALIDATE PLAN: OK (sin errores), {len(warnings)} warning(s)")
    else:
        print(f"✅ VALIDATE PLAN: OK — {len(tasks)} tareas, 0 errores, 0 warnings")
    print("=" * 70)

    for err in errors:
        print(err)
    for w in warnings:
        print(w)

    print(f"  Total tareas: {len(tasks)}")
    print(f"  IDs: {', '.join(sorted(all_ids))}")
    print()

    if has_errors:
        print("📋 RECOVERY:")
        print("  1. Corrige depends_on y preconditions en las tareas señaladas")
        print(f"     spring.py update-task {chain_dir} <task_id> --depends_on '[]'")
        print(f"     spring.py update-task {chain_dir} <task_id> --preconditions '[]'")
        print("  2. Vuelve a validar:")
        print(
            f"     python3 projects/delta-skill/scripts/spring.py validate-plan {chain_dir}"
        )
        print()
        return 1

    return 0


# ── Helpers ─────────────────────────────────────────────────────


def _get_nested(obj: dict, path: str) -> any:
    """Access nested dict value via dot-separated path (e.g. 'risks.curvature')."""
    parts = path.split(".")
    current = obj
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _set_nested(obj: dict, path: str, value: any) -> None:
    """Set nested dict value via dot-separated path (e.g. 'risks.curvature')."""
    parts = path.split(".")
    current = obj
    for part in parts[:-1]:
        if part not in current:
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value
