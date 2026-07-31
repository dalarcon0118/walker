#!/usr/bin/env python3
"""
delta-advance.py — Entry point único con Evidence + Meta-Instruction.

Determina automáticamente la siguiente tarea pendiente, ejecuta context
discovery, verifica predicates contra código real, muestra evidencia cruda
con meta-instruction, y luego invoca task.py advance con --task-id inyectado.

Uso:
    python3 scripts/delta-advance.py <chain_dir> [opciones]

    <chain_dir>    Ruta al directorio del sprint
    --confirm      Marcar como ready_for_review
    --force        Omitir enforcement gate
    --skip-qlty    Omitir quality gate Qlty
    --act-as po|dev|qa  Rol Scrum

Flujo:
    1. Lee sprint → determina siguiente task pending
    2. Context discovery con ast-grep (archivos afectados)
    3. Evidencia cruda: verifica cada predicate contra código real
    4. Assessment + Meta-instruction para el LLM
    5. Invoca task.py advance con --task-id inyectado
    6. Output auto-contextualizado

Exit codes (heredados de task.py advance):
    0 → Task completed (human confirmed)
    1 → Task partial — necesita REFINE
    2 → Error
    3 → ready_for_review — preguntar al usuario
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in [_BASE_DIR, os.path.join(_SCRIPT_DIR, "_internal")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _internal.verification import verify_predicate


# ── Helpers ─────────────────────────────────────────────────────


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def load_json(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def find_project_root(start: str | None = None) -> str:
    cwd = start or os.getcwd()
    cwd = os.path.abspath(cwd)
    for _ in range(10):
        if os.path.exists(os.path.join(cwd, ".cognitive")):
            return cwd
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent
    return os.path.abspath(os.getcwd())


# ── Sprint intelligence ─────────────────────────────────────────


def find_next_task(progress_data: dict, plan_data: dict) -> dict | None:
    """Find the next pending, in_progress, or ready_for_review task.

    Priority:
    1. ``next_action.task_id`` if its state is not completed
    2. First task with state pending/in_progress/ready_for_review
    3. None (all completed)
    """
    prog = progress_data.get("progress", progress_data)
    pl = plan_data.get("plan", plan_data)
    task_states = prog.get("current", {}).get("task_states", {})
    tasks: list[dict] = pl.get("tasks", [])

    _REVIEWABLE_STATES = ("pending", "in_progress", "ready_for_review", None)

    # Priority 1: next_action
    next_action = prog.get("current", {}).get("next_action", {})
    if next_action and isinstance(next_action, dict):
        tid = next_action.get("task_id")
        if tid and task_states.get(tid) in _REVIEWABLE_STATES:
            for t in tasks:
                if t.get("task_id") == tid:
                    return t

    # Priority 2: first active or reviewable task
    for t in tasks:
        tid = t.get("task_id", "")
        if task_states.get(tid) in _REVIEWABLE_STATES:
            return t

    return None


# ── Evidence engine ─────────────────────────────────────────────


def collect_predicate_evidence(task: dict, project_root: str) -> list[dict]:
    """Run ``verify_predicate`` on each validation predicate.

    Returns a list of dicts with keys:
        predicate, passed, info
    """
    predicates: list = task.get("validation", {}).get("predicates", [])
    evidence: list[dict] = []
    for pred in predicates:
        try:
            passed, info = verify_predicate(pred, project_root)
        except Exception as exc:
            passed = False
            info = {"method": "error", "error": str(exc), "label": str(pred)[:70]}
        evidence.append(
            {
                "predicate": pred,
                "passed": passed,
                "info": info,
            }
        )
    return evidence


def format_evidence_block(ev: dict, index: int, total: int) -> str:
    """Format a single evidence item as a human-readable block."""
    pred = ev["predicate"]
    passed = ev["passed"]
    info = ev.get("info", {})

    label = info.get("label", str(pred)[:70])
    method = info.get("method", "?")
    status_icon = "✅" if passed else "❌"
    status_text = "PASA" if passed else "FALLA"

    lines = [f"  [{index}/{total}] {status_icon} Predicate:"]

    # Predicate text (compact)
    pred_str = str(pred)
    if len(pred_str) > 100:
        pred_str = pred_str[:100] + "…"
    lines.append(f"       {pred_str}")
    lines.append(f"       Label: {label}")
    lines.append(f"       Método: {method}")

    # Method-specific evidence
    if method == "shell":
        lines.append(f"       Comando: {info.get('command', '?')}")
        lines.append(f"       Exit code: {info.get('exit_code', '?')}")
        stdout = info.get("stdout", "")
        if stdout:
            lines.append("       stdout:")
            for line in stdout.splitlines()[:5]:
                lines.append(f"         {line}")
        stderr = info.get("stderr", "")
        if stderr:
            lines.append(f"       stderr ({len(stderr.splitlines())} lines):")
            for line in stderr.splitlines()[:3]:
                lines.append(f"         {line}")

    elif method in ("symbol_exists", "symbol_not_exists"):
        lines.append(f"       Símbolo: {info.get('symbol', '?')}")
        if info.get("file"):
            lines.append(f"       Archivo: {info['file']}")

    elif method == "command_output":
        lines.append(f"       Comando: {info.get('command', '?')}")
        lines.append(f'       Buscaba: "{info.get("contains", "?")}"')
        found = info.get("found", False)
        lines.append(f"       Encontrado: {'✅ sí' if found else '❌ no'}")
        stdout = info.get("stdout", "")
        if stdout:
            lines.append("       stdout:")
            for line in stdout.splitlines()[:5]:
                lines.append(f"         {line}")

    elif method in ("ast-grep",):
        lines.append(f"       Pattern: {info.get('pattern', '?')}")
        lines.append(f"       Tipo: {info.get('type', '?')}")
        if info.get("file_hint"):
            lines.append(f"       Archivo: {info['file_hint']}")

    elif method == "test_pass":
        lines.append(f"       Comando: {info.get('command', '?')}")
        lines.append(f"       Exit code: {info.get('exit_code', '?')}")

    elif method == "error":
        lines.append(f"       ⚠️  Error: {info.get('error', '?')[:120]}")

    if info.get("error") and method != "error":
        lines.append(f"       ⚠️  Error: {info['error'][:100]}")

    lines.append(f"       → {status_icon} {status_text}")
    lines.append("")

    return "\n".join(lines)


def print_context_discovery(chain_dir: str, task: dict) -> None:
    """Print context discovery: affected files and APIs."""
    artefacts = task.get("impacted_artifacts", {})
    files: list[str] = artefacts.get("files", [])
    apis: list[str] = artefacts.get("apis", [])
    project_root = find_project_root()

    print("  📂 ARCHIVOS AFECTADOS:")
    if not files:
        print("    (ninguno)")
    for f in files:
        candidate = os.path.join(project_root, f)
        if os.path.exists(candidate):
            print(f"    ✅ EXISTE: {f}")
        else:
            print(f"    🆕 NUEVO:  {f}")

    if apis:
        print("  🔌 APIS / SÍMBOLOS A CREAR:")
        for api in apis:
            print(f"    🆕 {api}")

    print()


# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════


def main() -> int:
    # ── Parse arguments ─────────────────────────────────────────
    # Handle --act-as separately: its value is NOT a positional arg
    args = sys.argv[1:]
    act_as_index = None
    act_as_value = None
    for i, a in enumerate(args):
        if a == "--act-as":
            act_as_index = i
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                act_as_value = args[i + 1]
            break

    skip_indices: set[int] = set()
    if act_as_index is not None:
        skip_indices.add(act_as_index)
        if act_as_value is not None:
            skip_indices.add(act_as_index + 1)

    positional = [
        a
        for i, a in enumerate(args)
        if not a.startswith("--") and i not in skip_indices
    ]
    flags = set(a for a in args if a.startswith("--"))

    if len(positional) != 1:
        print("=" * 70)
        print("  delta-advance.py — Entry point único Evidence + Meta-Instruction")
        print("=" * 70)
        print()
        print("  Uso: python3 scripts/delta-advance.py <chain_dir> [opciones]")
        print()
        print("  <chain_dir>    Ruta al directorio del sprint")
        print("  --confirm      Marcar como ready_for_review")
        print("  --force        Omitir enforcement gate (desviación del goal)")
        print("  --skip-qlty    Omitir quality gate Qlty")
        print("  --act-as       Rol Scrum: po|dev|qa")
        print()
        print("  El script determina automáticamente la siguiente tarea")
        print("  pendiente y ejecuta task.py advance con --task-id inyectado.")
        print()
        print("  Exit codes:")
        print("    0 → Task completed (human confirmed)")
        print("    1 → Task partial — necesita REFINE")
        print("    2 → Error")
        print("    3 → ready_for_review — preguntar al usuario")
        print("=" * 70)
        return 2

    chain_dir = os.path.abspath(positional[0])
    project_root = find_project_root(chain_dir)

    if not os.path.isdir(chain_dir):
        eprint(f"❌ Directorio no encontrado: {chain_dir}")
        return 2

    # ── Load sprint data ────────────────────────────────────────
    plan_path = os.path.join(chain_dir, "plan.json")
    progress_path = os.path.join(chain_dir, "progress.json")

    plan = load_json(plan_path)
    progress = load_json(progress_path)

    if not plan:
        eprint(f"❌ plan.json no encontrado en {chain_dir}")
        return 2
    if not progress:
        eprint(f"❌ progress.json no encontrado en {chain_dir}")
        return 2

    pl = plan.get("plan", plan)
    prog = progress.get("progress", progress)
    chain_id = pl.get("meta", {}).get("chain_id", os.path.basename(chain_dir))

    # ── Find next task ──────────────────────────────────────────
    task = find_next_task(progress, plan)
    if not task:
        print("=" * 70)
        print("🎯 TODAS LAS TAREAS COMPLETADAS")
        print("=" * 70)
        print("  No hay tareas pendientes en este sprint.")
        print("  Ejecuta CLOSURE para cerrar el sprint:")
        print(f"    python3 scripts/spring.py close {chain_dir}")
        print()
        print("  Para ver el estado:")
        print(f"    python3 scripts/spring.py status {chain_dir}")
        print("=" * 70)
        return 0

    task_id: str = task.get("task_id", "")
    task_states = prog.get("current", {}).get("task_states", {})
    current_state: str = task_states.get(task_id, "pending")

    if current_state == "completed":
        print(f"⚠️  La tarea {task_id} ya está completada.")
        print("  Buscando siguiente tarea pendiente…")
        # Force scan for another task
        task = find_next_task(progress, plan)
        if not task:
            print("  (ninguna encontrada — sprint completo)")
            return 0
        task_id = task.get("task_id", "")
        current_state = task_states.get(task_id, "pending")
        if current_state == "completed":
            print("❌ No hay tareas pendientes.")
            return 0

    # ═════════════════════════════════════════════════════════════
    #  HUMAN CONFIRM SHORTCUT: skip evidence for already-reviewed tasks
    # ═════════════════════════════════════════════════════════════
    is_human_confirm = (
        current_state == "ready_for_review" and "--human-confirm" in flags
    )
    if is_human_confirm:
        print("=" * 70)
        print(f"✅ CONFIRMANDO {task_id} — Tarea ya evaluada, confirmación humana")
        print("=" * 70)
        print(f"  Sprint:    {chain_id}")
        print(f"  Tarea:     {task_id}")
        print(f"  Objetivo:  {task.get('objective', '')[:100]}")
        print()

    # ═════════════════════════════════════════════════════════════
    #  EVIDENCE + META-INSTRUCTION BLOCK (skip for human-confirm)
    # ═════════════════════════════════════════════════════════════
    if not is_human_confirm:
        if current_state == "ready_for_review":
            print(f"⚠️  La tarea {task_id} ya está lista para revisión.")
            print("  Para confirmar como PO:")
            print(
                f"    python3 scripts/delta-advance.py {chain_dir} --act-as po --confirm --human-confirm"
            )
            print("  Para confirmar como Dev:")
            print(f"    python3 scripts/delta-advance.py {chain_dir} --confirm")
            return 3

        print("=" * 70)
        print("🚀 DELTA ADVANCE — Siguiente tarea detectada automáticamente")
        print("=" * 70)
        print(f"  Sprint:    {chain_id}")
        print(f"  Tarea:     {task_id}")
        print(f"  Objetivo:  {task.get('objective', '')[:100]}")
        print(f"  Estado:    {current_state}")
        print(f"  Tipo:      {task.get('type', '?')}")
        print(f"  Dominio:   {task.get('domain', '?')}")
        print()

        # ── 1. Context Discovery ────────────────────────────────────
        print("─" * 70)
        print("🔍 CONTEXT DISCOVERY — Estado real de archivos y símbolos")
        print("─" * 70)
        print_context_discovery(chain_dir, task)

        # ── 2. Raw Evidence ─────────────────────────────────────────
        predicates = task.get("validation", {}).get("predicates", [])
        if not predicates:
            print("  ⚠️  Esta tarea no tiene predicates de validación definidos.")
            print("  (task.validation.predicates está vacío)")
            print()

        if predicates:
            print("─" * 70)
            print("📊 EVIDENCIA CRUDA — Verificación de predicates contra código real")
            print("─" * 70)
            print()

            evidence = collect_predicate_evidence(task, project_root)
            passed_count = sum(1 for e in evidence if e["passed"])
            total_count = len(evidence)

            for i, ev in enumerate(evidence, 1):
                print(format_evidence_block(ev, i, total_count))

            # ── Summary ───────────────────────────────────────────
            pct = round(passed_count / max(total_count, 1) * 100)
            print(f"  📊 RESUMEN: {passed_count}/{total_count} predicates ({pct}%)")
            print()

            # ── Assessment ─────────────────────────────────────────
            print("─" * 70)
            print("📋 ASSESSMENT")
            print("─" * 70)
            if passed_count == total_count:
                print("  ✅ Todos los predicates están satisfechos.")
                print(
                    "  El código implementado cumple con todos los criterios de validación."
                )
            elif pct >= 80:
                print(
                    f"  ⚠️  {passed_count}/{total_count} predicates satisfechos ({pct}%)."
                )
                print(
                    "  Cobertura alta pero incompleta. Revisa los predicates fallidos arriba."
                )
            else:
                print(
                    f"  ❌ Solo {passed_count}/{total_count} predicates satisfechos ({pct}%)."
                )
                print(
                    "  Cobertura insuficiente. Implementa el código faltante primero."
                )
            print()

            # ── Failed details ─────────────────────────────────────
            failed = [e for e in evidence if not e["passed"]]
            if failed:
                print("  ❌ Predicates FALLIDOS:")
                for f in failed:
                    label = f["info"].get("label", str(f["predicate"])[:70])
                    print(f"     · {label}")
                    if f["info"].get("error"):
                        print(f"       ⚠️  {f['info']['error'][:100]}")
                    # Also show the raw predicate for copy-paste debugging
                    raw = str(f["predicate"])
                    if len(raw) > 60:
                        print(f"       Predicate: {raw[:60]}…")
                    else:
                        print(f"       Predicate: {raw}")
                print()

        # ── 3. Meta-Instruction ─────────────────────────────────────
        print("=" * 70)
        print("🧠 META-INSTRUCTION — Instrucción para el LLM")
        print("=" * 70)
        print()
        print("  El bloque EVIDENCIA CRUDA arriba contiene la verificación real")
        print("  de cada predicate contra el código en disco. Los datos incluyen:")
        print("  - stdout/stderr de comandos ejecutados")
        print("  - Líneas exactas de código encontradas por grep/ast-grep")
        print("  - Exit codes de validaciones")
        print("  - Assessment detallado por cada predicate")
        print()
        print("  ⚠️  DEBES presentar esta evidencia al usuario ANTES de pedir")
        print("     confirmación para continuar. No confirmes sin mostrar datos.")
        print()
        print("  ▶️  Si ves predicates fallidos arriba, implementa el código")
        print("     faltante y luego ejecuta este mismo comando de nuevo:")
        print(f"     python3 scripts/delta-advance.py {chain_dir}")
        print()
        print("  ▶️  Si todos los predicates pasan, el comando a continuación")
        print("     ejecutará el pipeline completo de task.py advance.")
        print()
        print("  ▶️  Observa la salida de task.py advance para ver si el")
        print("     TASK LOOP alcanza el result_state.")
        print()
        print("=" * 70)

    # ── 4. Invoke task.py advance ───────────────────────────────
    print()
    print("─" * 70)
    print(f"🔄 Invocando: python3 scripts/task.py advance <chain> {task_id}")
    print("─" * 70)
    print()

    task_py = os.path.join(_SCRIPT_DIR, "task.py")
    task_args = [sys.executable, task_py, "advance", chain_dir, task_id]

    # Forward relevant flags
    flag_map = ["--confirm", "--force", "--skip-qlty", "--human-confirm"]
    for flag in flag_map:
        if flag in flags:
            task_args.append(flag)

    # Forward --act-as (including its value)
    for i, a in enumerate(sys.argv):
        if a == "--act-as":
            if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
                task_args.append("--act-as")
                task_args.append(sys.argv[i + 1])
            break

    try:
        result = subprocess.run(task_args, timeout=300)
        return result.returncode
    except subprocess.TimeoutExpired:
        eprint("❌ Timeout ejecutando task.py advance (300s)")
        return 2
    except FileNotFoundError:
        eprint(f"❌ No se encontró: {task_py}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
