#!/usr/bin/env python3
"""delta-close.py — Cierra formalmente un spring delta.

Uso:
    python3 scripts/delta-close.py <chain_dir> [--dry-run] [--force]

Donde <chain_dir> es la ruta al directorio del chain (contiene plan.json y progress.json).

Flujo:
    1. Valida que todas las tareas estén completadas
    2. Escanea riesgos: detecta archivos referenciados que ya no existen → obsoletos
    3. Cierra el spring: meta.status = "closed", trail entry final, finished = true
    4. Limpia current.json si este chain era el foco activo
    5. Genera reporte de cierre con métricas + riesgos

--dry-run: muestra lo que haría sin modificar archivos
--force: cierra incluso si hay tareas pendientes

Exit codes:
    0 → Cierre completado
    1 → Error / validación fallida
    2 → Error de uso

Ejemplo:
    python3 scripts/delta-close.py .cognitive/delta/mi-chain
    python3 scripts/delta-close.py .cognitive/delta/mi-chain --dry-run
    python3 scripts/delta-close.py .cognitive/delta/mi-chain --force
"""

import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone

# ── KV helpers (same pattern as delta-advance.py) ──────────────
_HAVE_KV = False
try:
    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(__file__),
            "../../../projects/cognitive-mcp/cognitive_v2/shared",
        ),
    )
    from helpers import _kv, _kv_set

    _HAVE_KV = True
except ImportError:
    pass


def _kv_load(
    chain_id: str, data_type: str, project_id: str | None = None
) -> dict | None:
    """Load sprint data from KV, fallback to None.

    In multi-project mode (project_id provided), uses
    ``kv:sprint:{project_id}:{chain_id}:{data_type}`` to avoid key
    collisions between projects. Falls back to legacy key
    ``kv:sprint:{chain_id}:{data_type}`` if not found.
    """
    if not _HAVE_KV:
        return None
    try:
        if project_id:
            key = f"kv:sprint:{project_id}:{chain_id}:{data_type}"
            raw = _kv(key)
            if raw:
                if isinstance(raw, dict):
                    return raw
                if isinstance(raw, str):
                    return json.loads(raw)
        key = f"kv:sprint:{chain_id}:{data_type}"
        raw = _kv(key)
        if raw and isinstance(raw, dict):
            return raw
        if raw and isinstance(raw, str):
            return json.loads(raw)
    except Exception:
        pass
    return None


def _kv_save(
    chain_id: str, data_type: str, data: dict, project_id: str | None = None
) -> bool:
    """Save sprint data to KV.

    In multi-project mode (project_id provided), writes to
    ``kv:sprint:{project_id}:{chain_id}:{data_type}`` for isolation.
    Legacy: writes to ``kv:sprint:{chain_id}:{data_type}``.
    """
    if not _HAVE_KV:
        return False
    try:
        ns = f"{project_id}:{chain_id}" if project_id else chain_id
        key = f"kv:sprint:{ns}:{data_type}"
        _kv_set(key, data)
        return True
    except Exception:
        return False


# ── projects.py import (for multi-project resolution) ──────────
_SCRIPT_DIR_CLOSE = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR_CLOSE = os.path.dirname(_SCRIPT_DIR_CLOSE)
if _PARENT_DIR_CLOSE not in sys.path:
    sys.path.insert(0, _PARENT_DIR_CLOSE)

try:
    from _internal.projects import ProjectRegistry, ProjectProfile

    _HAVE_PROJECTS = True
except ImportError:
    _HAVE_PROJECTS = False


def _get_project_id(plan: dict) -> str | None:
    """Extract ``project_id`` from plan data.

    Returns ``plan.plan.project.project_id``, or None if not present
    (legacy single-project format → backward compat).
    """
    try:
        pl = plan.get("plan", plan)
        return pl.get("project", {}).get("project_id") or None
    except Exception:
        return None


def eprint(*args, **kwargs):
    """Print to stderr."""
    print(*args, file=sys.stderr, **kwargs)


def load_json(path):
    """Load a JSON file with error handling.

    For ``progress.json``, verifies the ``_checksum`` field (SHA-256 integrity).
    Exits with error if checksum doesn't match (file tampered externally).
    """
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        eprint(f"❌ ERROR: Archivo no encontrado: {path}")
        sys.exit(2)
    except json.JSONDecodeError as e:
        eprint(f"❌ ERROR: JSON inválido en {path}: {e}")
        sys.exit(2)

    # Integrity check for progress.json
    if os.path.basename(path) == "progress.json":
        try:
            from _internal.progress_checksum import verify_checksum

            if not verify_checksum(data):
                eprint(
                    "❌ ERROR: progress.json manipulado externamente — checksum no coincide"
                )
                eprint(f"   Archivo: {path}")
                eprint(
                    "   El archivo fue editado directamente sin usar los scripts del skill."
                )
                sys.exit(2)
        except ImportError:
            eprint(
                "  ⚠️  progress_checksum no disponible — saltando verificación de integridad"
            )
            eprint(f"     Archivo: {path}")

    return data


def write_json(path, data):
    """Write a JSON file.

    For ``progress.json``, adds a ``_checksum`` field (SHA-256 integrity hash).
    """
    # Add integrity checksum for progress.json
    if os.path.basename(path) == "progress.json":
        try:
            from _internal.progress_checksum import add_checksum

            add_checksum(data)
        except ImportError:
            eprint(
                "  ⚠️  progress_checksum no disponible — escribiendo progress.json sin checksum"
            )

    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  📝 Escrito: {path}")


def find_project_root():
    """Find the project root (where .cognitive/ lives)."""
    cwd = os.getcwd()
    for _ in range(5):
        if os.path.exists(os.path.join(cwd, ".cognitive")):
            return cwd
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent
    return os.getcwd()


# ── File patterns to scan for in risk descriptions ──────────────
_FILE_REFERENCE_RE = re.compile(
    r"[\w/.-]+\.(?:py|js|ts|jsx|tsx|json|yaml|yml|md|txt|cfg|ini|toml|sh|sql|css|html)"
)


def extract_file_refs(text: str) -> list[str]:
    """Extract Python / JS / config file references from text.

    Returns deduplicated list of referenced filenames or paths.
    Filters out obvious non-path tokens like version numbers.
    """
    refs = []
    for m in _FILE_REFERENCE_RE.finditer(text):
        path = m.group(0)
        # Skip short tokens that happen to have a dot (e.g. "v2.0", "3.14")
        if len(path) < 6:
            continue
        # Skip numeric-only prefixes
        if re.match(r"^\d+\.", path):
            continue
        refs.append(path)
    return list(set(refs))


def detect_obsolete_risks(risks: list[dict], project_root: str) -> list[dict]:
    """Scan risk descriptions for file references and check if files exist.

    Returns updated risk list with ``status`` field added:
    - ``"valid"`` — all referenced files exist
    - ``"obsolete"`` — one or more referenced files no longer exist
    - ``"unchecked"`` — no file references found in description
    """
    results = []
    for risk in risks:
        desc = risk.get("description", "")
        refs = extract_file_refs(desc)
        if not refs:
            risk["status"] = "unchecked"
            results.append(risk)
            continue

        missing = []
        for ref in refs:
            # Try direct path
            candidate = os.path.join(project_root, ref)
            if os.path.exists(candidate):
                continue
            # Try under projects/ with recursive walk
            projects_dir = os.path.join(project_root, "projects")
            if os.path.isdir(projects_dir):
                found = False
                for sub in os.listdir(projects_dir):
                    sub_path = os.path.join(projects_dir, sub)
                    if not os.path.isdir(sub_path):
                        continue
                    for root, dirs, files in os.walk(sub_path):
                        dirs[:] = [
                            d
                            for d in dirs
                            if d
                            not in (
                                "__pycache__",
                                ".git",
                                "node_modules",
                                ".venv",
                                "venv",
                                "dist",
                                "build",
                                ".cognitive",
                                ".agents",
                                ".opencode",
                            )
                        ]
                        basename = os.path.basename(ref)
                        if basename in files:
                            found = os.path.join(root, basename)
                            break
                    if found:
                        break
                if found:
                    continue
            missing.append(ref)

        if missing:
            risk["status"] = "obsolete"
            risk["obsolete_files"] = missing
            print(f"  ⚠️  Riesgo obsoleto: {risk.get('description', '')[:70]}...")
            for m in missing:
                print(f"      → Archivo no encontrado: {m}")
        else:
            risk["status"] = "valid"

        results.append(risk)
    return results


def close_sprint(chain_dir: str, dry_run: bool = False, force: bool = False):
    """Execute the spring closure pipeline.

    Returns 0 on success, 1 on failure.
    """
    progress_path = os.path.join(chain_dir, "progress.json")
    plan_path = os.path.join(chain_dir, "plan.json")
    chain_id = os.path.basename(os.path.normpath(chain_dir))
    project_root = find_project_root()

    # ── Read data (KV first, then JSON) ──────────────────────────
    plan = _kv_load(chain_id, "plan")
    if not plan:
        if not os.path.exists(plan_path):
            eprint(f"❌ ERROR: plan.json no encontrado en {chain_dir}")
            sys.exit(2)
        plan = load_json(plan_path)
    project_id = _get_project_id(plan)

    progress = _kv_load(chain_id, "progress", project_id)
    if not progress:
        if not os.path.exists(progress_path):
            eprint(f"❌ ERROR: progress.json no encontrado en {chain_dir}")
            sys.exit(2)
        progress = load_json(progress_path)

    # Normalize nested formats (v2.x vs v1.0)
    pl = plan.get("plan", plan)
    prog = progress.get("progress", progress)

    meta = prog.setdefault("meta", {})
    current = prog.setdefault("current", {})
    task_states = current.setdefault("task_states", {})
    trail = prog.setdefault("trail", [])
    risks = current.setdefault("risks", [])

    # Load task definitions from plan
    tasks = pl.get("tasks", [])

    print("=" * 70)
    print(f"🔒 CERRANDO SPRING: {meta.get('chain_id', chain_id)}")
    print("=" * 70)
    print(f"📍 Project root: {project_root}")
    print(f"📂 Chain:        {chain_dir}")
    if dry_run:
        print("🔧 Mode:         --dry-run (sin cambios)")
    print()

    # ── 1. Validate all tasks completed ─────────────────────────
    # Use max(tasks, task_states) to handle tasks added mid-sprint
    total_tasks = max(len(tasks), len(task_states))
    completed_tasks = sum(1 for t in task_states.values() if t == "completed")
    pending_tasks = [
        tid
        for tid, state in task_states.items()
        if state in ("pending", "in_progress", "ready_for_review")
    ]

    print(f"📊 Tareas: {completed_tasks}/{total_tasks} completadas")

    if pending_tasks:
        print(f"  ❌ Tareas pendientes: {', '.join(pending_tasks)}")
        if not force:
            eprint()
            eprint(
                "❌ ERROR: Hay tareas pendientes. Usa --force para cerrar igualmente,"
                " o completa las tareas primero con delta-advance.py."
            )
            eprint(f"   Pendientes: {', '.join(pending_tasks)}")
            sys.exit(1)
        print("  ⚠️  --force activo: cerrando con tareas pendientes")

    # ── 2. Detect obsolete risks ─────────────────────────────────
    print()
    total_risks = len(risks)
    risks_updated = detect_obsolete_risks(risks, project_root)
    obsolete_count = sum(1 for r in risks_updated if r.get("status") == "obsolete")
    valid_count = sum(1 for r in risks_updated if r.get("status") == "valid")
    unchecked_count = sum(1 for r in risks_updated if r.get("status") == "unchecked")

    if total_risks > 0:
        print(f"\n📋 Riesgos: {total_risks} total")
        print(f"  ✅ Válidos:     {valid_count}")
        if obsolete_count > 0:
            print(
                f"  ⚠️  Obsoletos:   {obsolete_count} (archivos referenciados ya no existen)"
            )
        if unchecked_count > 0:
            print(f"  ➖ Sin verificar: {unchecked_count} (sin referencias a archivos)")
    else:
        print("📋 Riesgos: ninguno registrado")

    # ── 3. Compute closure summary ───────────────────────────────
    # Count satisfied predicates from completed tasks
    total_predicates = sum(
        [
            len(pl.get("goal", {}).get("predicates", {}).get("must_exist", [])),
            len(pl.get("goal", {}).get("predicates", {}).get("must_not_exist", [])),
            len(pl.get("goal", {}).get("predicates", {}).get("invariants", [])),
        ]
    )
    completed_predicates = set()
    for t in tasks:
        if task_states.get(t["task_id"]) == "completed":
            for p in t.get("validation", {}).get("predicates", []):
                completed_predicates.add(p)
    satisfied = len(completed_predicates)
    distance = max(0.0, 1.0 - (satisfied / max(total_predicates, 1)))

    # Build objective summary from plan
    goal_desc = pl.get("goal", {}).get("description", "N/A")[:120]

    # ════════════════════════════════════════════════════════════════
    #  ENFORCEMENT: Composability check — ALL predicates must be
    #  satisfied for a sprint to close. Partial closure is an
    #  antipattern. This is NON-NEGOTIABLE (no --force bypass).
    # ════════════════════════════════════════════════════════════════
    if total_predicates > 0 and satisfied < total_predicates:
        unsatisfied_preds = []
        for p in pl.get("goal", {}).get("predicates", {}).get("must_exist", []):
            if p not in completed_predicates:
                unsatisfied_preds.append(p)
        for p in pl.get("goal", {}).get("predicates", {}).get("must_not_exist", []):
            if p not in completed_predicates:
                unsatisfied_preds.append(p)
        for p in pl.get("goal", {}).get("predicates", {}).get("invariants", []):
            if p not in completed_predicates:
                unsatisfied_preds.append(p)

        eprint()
        eprint("=" * 70)
        eprint(
            f"❌ COMPOSITION BLOCKED: {satisfied}/{total_predicates} predicates satisfechos"
        )
        eprint("=" * 70)
        eprint(
            "  Un sprint SOLO se cierra cuando TODOS los predicates están satisfechos."
        )
        eprint(f"  Faltan {total_predicates - satisfied} predicates por completar:")
        for up in unsatisfied_preds:
            eprint(f"    • {up}")
        eprint()
        eprint("  Acciones requeridas:")
        eprint("  1. Identifica qué tareas cubren los predicates faltantes")
        eprint(
            "  2. Avanza esas tareas: python3 scripts/task.py advance <chain> <task_id>"
        )
        eprint("  3. Una vez completadas todas, reintenta el cierre")
        eprint()
        eprint(
            "  NOTA: --force NO permite saltar predicates. El cierre es composicional."
        )
        eprint("  Si el goal cambió, crea un NUEVO sprint con el nuevo G_objetivo.")
        eprint("=" * 70)
        return 1

    # ── 4. Build closure trail entry ─────────────────────────────
    closure_entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "distance": round(distance, 4),
        "event": f"🔒 SPRINT CLOSED. {completed_tasks}/{total_tasks} tareas completadas. "
        f"Satisfice {satisfied}/{total_predicates} predicates. "
        f"Riesgos: {valid_count} validos, {obsolete_count} obsoletos.",
        "closure": {
            "tasks_completed": completed_tasks,
            "tasks_total": total_tasks,
            "predicates_satisfied": satisfied,
            "predicates_total": total_predicates,
            "distance": round(distance, 4),
            "risks_valid": valid_count,
            "risks_obsolete": obsolete_count,
            "risks_unchecked": unchecked_count,
        },
    }

    # ── 5. Apply changes ─────────────────────────────────────────
    if not dry_run:
        # Update meta status
        meta["status"] = "closed"
        meta["closed_at"] = datetime.now(timezone.utc).isoformat()

        # Append closure trail entry
        trail.append(closure_entry)

        # Clear next_action, mark finished
        current["next_action"] = None
        current["finished"] = True

        # Update delta_metrics with closure info
        delta_metrics = current.setdefault("delta_metrics", {})
        delta_metrics["closure"] = {
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "satisfied_predicates": satisfied,
            "total_predicates": total_predicates,
            "distance_at_close": round(distance, 4),
            "risks_obsolete": obsolete_count,
            "risks_total": total_risks,
        }

        # Write updated progress.json
        progress_data = {"progress": prog} if "progress" in progress else prog
        write_json(progress_path, progress_data)

        # Save to KV if available (project-scoped when project_id set)
        _kv_save(chain_id, "progress", progress_data, project_id)

        # ── 6. Update current.json ────────────────────────────────
        current_json_path = os.path.join(
            os.path.dirname(os.path.abspath(chain_dir)), "current.json"
        )
        if os.path.exists(current_json_path):
            try:
                if os.path.islink(current_json_path):
                    os.unlink(current_json_path)
                shutil.copy(progress_path, current_json_path)
                print("  📋 current.json actualizado con estado de cierre")
            except Exception as e:
                eprint(f"  ⚠️  No se pudo actualizar current.json: {e}")

        print()
        print("─" * 70)
        print("✅ SPRING CERRADO EXITOSAMENTE")
    else:
        print()
        print("─" * 70)
        print("🔍 DRY-RUN — no se escribieron cambios")
        print(f"   Se escribiría: {progress_path}")
        if os.path.exists(os.path.join(os.path.dirname(chain_dir), "current.json")):
            print("   Se actualizaría: current.json")

    # ── 7. Print closure dashboard ───────────────────────────────
    print()
    print("📊 SPRING CLOSURE DASHBOARD")
    print("─" * 70)
    print(f"  Chain ID:     {meta.get('chain_id', chain_id)}")
    print(f"  Goal:         {goal_desc}")
    print("  Status:       🔒 CLOSED")
    print(f"  Tareas:       {completed_tasks}/{total_tasks}")
    if pending_tasks:
        print(f"  Pendientes:   {', '.join(pending_tasks)}")
    print(
        f"  Predicates:   {satisfied}/{total_predicates} ({round(distance * 100)}% distance)"
    )
    print(f"  Riesgos:      {valid_count} válidos, {obsolete_count} obsoletos")
    print()
    print("📋 RIESGOS DETECTADOS:")
    if total_risks == 0:
        print("  (ninguno)")
    else:
        for r in risks_updated:
            status_icon = {
                "valid": "✅",
                "obsolete": "⚠️  OBSOLETO",
                "unchecked": "➖",
            }.get(r.get("status", ""), "❓")
            severity_icon = {
                "high": "🔴",
                "medium": "🟡",
                "low": "🟢",
            }.get(r.get("severity", ""), "⚪")
            print(
                f"  {severity_icon} [{status_icon:>12}] {r.get('description', '')[:80]}"
            )

    print()
    print("📈 EVOLUCIÓN:")
    print(f"  Trail entries: {len(trail)}")
    print(f"  Distance final: {round(distance, 4)}")
    print()

    # Show metric deltas: trail first vs computed final
    if len(trail) >= 2:
        first_d = trail[0].get("distance", 1.0)
        # Use computed distance for last_d (trail values may be stale)
        last_d = round(distance, 4)
        delta_pct = round((first_d - last_d) * 100, 1)
        print(f"  Distance inicial: {first_d} (trail)")
        print(f"  Distance final:   {last_d} (computed)")
        print(f"  Δ:                -{delta_pct}%")
    print("─" * 70)

    # ── 8. DECISION PROTOCOL (self-sufficient output) ─────────────
    print()
    print("─" * 70)
    print("📋 DECISION PROTOCOL — siguiente paso:")
    print()

    if pending_tasks and not dry_run:
        # Force-close with pending tasks
        print("   ⚠️  Spring cerrado con tareas pendientes (--force).")
        print("   ▶️  Registrar las tareas no completadas como deuda técnica")
        print("       o crear un nuevo sprint para completarlas.")
        print(f"      Pendientes: {', '.join(pending_tasks)}")
    elif obsolete_count > 0:
        print("   ⚠️  Se detectaron riesgos obsoletos (archivos ya no existen).")
        print("   ▶️  Los riesgos obsoletos se marcaron automáticamente.")
        print("       No requieren acción inmediata pero revisa la lista.")
    else:
        print("   ✅ Composición válida — todos los predicates satisfechos.")

    backlog_path_hint = (
        f".cognitive/projects/{project_id}/delta/backlog.jsonl"
        if project_id
        else ".cognitive/delta/backlog.jsonl"
    )
    print()
    print("   ▶️  Nuevo ciclo: define un nuevo G_objetivo y crea otro sprint.")
    print(f"   ▶️  Revisar backlog: {backlog_path_hint}")
    print()
    print("💡 Sprint cerrado. El LLM solo necesita decidir el próximo objetivo.")
    print("=" * 70)

    return 0


def main():
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(positional) != 1:
        print("Uso: python3 scripts/delta-close.py <chain_dir> [--dry-run] [--force]")
        print()
        print("Cierra formalmente un spring delta.")
        print()
        print(
            "  <chain_dir>   Ruta al directorio del chain (contiene plan.json + progress.json)"
        )
        print("  --dry-run     Muestra lo que haría sin modificar archivos")
        print("  --force       Cierra incluso si hay tareas pendientes")
        print()
        print("Exit codes:")
        print("  0 → Cierre completado")
        print("  1 → Error / validación fallida")
        print("  2 → Error de uso")
        sys.exit(2)

    chain_dir = positional[0]
    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv

    if not os.path.isdir(chain_dir):
        eprint(f"❌ ERROR: Directorio no encontrado: {chain_dir}")
        sys.exit(2)

    # Validate that plan.json and progress.json exist
    plan_path = os.path.join(chain_dir, "plan.json")
    progress_path = os.path.join(chain_dir, "progress.json")

    if not os.path.exists(plan_path):
        eprint(f"❌ ERROR: plan.json no encontrado en {chain_dir}")
        sys.exit(2)

    if not os.path.exists(progress_path):
        eprint(f"❌ ERROR: progress.json no encontrado en {chain_dir}")
        sys.exit(2)

    # Check if already closed
    try:
        with open(progress_path) as f:
            existing = json.load(f)
        existing_prog = existing.get("progress", existing)
        existing_status = existing_prog.get("meta", {}).get("status")
        if existing_status == "closed" and not force:
            existing_closed_at = existing_prog.get("meta", {}).get(
                "closed_at", "unknown"
            )
            eprint(f"⚠️  Este spring ya está cerrado (desde {existing_closed_at}).")
            eprint("   Usa --force para regenerar el cierre.")
            sys.exit(1)
    except (json.JSONDecodeError, KeyError):
        pass

    sys.exit(close_sprint(chain_dir, dry_run=dry_run, force=force))
