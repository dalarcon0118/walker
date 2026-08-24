"""
application/ceremonies.py — Scrum ceremonies for the delta skill.

Migrado desde ``to_clean_next/_internal/ceremonies.py``.

Product Backlog management + Sprint Planning ceremony + Human Confirm gate.

Backlog items se almacenan en JSONL en <project>/delta/backlog.jsonl.
Planning proposals se almacenan en <chain_dir>/.planning/proposal.json.

PRINCIPIO: el estado de negocio del spring (goal, specs, status) vive en
el DELTA (SpringService — features/delta_tracking/). plan.json NO existe
como fuente paralela (ADR-WALKER-013); el commit crea spring + specs en
el DeltaGraph.

La síntesis de tareas desde predicates (tipada + files reales + riesgo)
vive en ``features/spring/application/task_synthesis.py`` (port desde el
legacy ``deep_analysis``). El protocolo de 6 capas interactivo NO se porta.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

from features.delta_tracking.application.task_synthesis import (
    collect_known_files,
    enrich_tasks_with_predicates,
    sanitize_chain_id,
)


# ── Helpers ────────────────────────────────────────────────────


def eprint(*args: Any, **kwargs: Any) -> None:
    print(*args, file=sys.stderr, **kwargs)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_json(path: str, data: dict) -> None:
    """Escribe un dict como JSON indentado (proposal del planning)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Backlog IDs ────────────────────────────────────────────────

BACKLOG_ID_RE = re.compile(r"BL-(\d+)")


def _next_backlog_id(items: list[dict]) -> str:
    """Generate next BL-NNN id."""
    max_n = 0
    for it in items:
        m = BACKLOG_ID_RE.match(it.get("id", ""))
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"BL-{max_n + 1:03d}"


# ── Backlog persistence ────────────────────────────────────────


def _backlog_path(backlog_dir: str) -> str:
    """Get the path to the backlog JSONL file."""
    return os.path.join(backlog_dir, "backlog.jsonl")


def _load_backlog(backlog_dir: str) -> list[dict]:
    """Load all items from a backlog JSONL file."""
    path = _backlog_path(backlog_dir)
    if not os.path.exists(path):
        return []
    items: list[dict] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    eprint(f"  ⚠️  Línea inválida en backlog: {line[:60]}")
    # Sort by priority_score descending
    items.sort(key=lambda x: x.get("priority_score", 0), reverse=True)
    return items


def _save_backlog(backlog_dir: str, items: list[dict]) -> None:
    """Write all items to backlog JSONL."""
    path = _backlog_path(backlog_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def _append_backlog(backlog_dir: str, item: dict) -> None:
    """Append a single item to backlog JSONL."""
    path = _backlog_path(backlog_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def _prioritize(items: list[dict]) -> list[dict]:
    """Sort items by priority_score descending, then assign rank."""
    items.sort(key=lambda x: x.get("priority_score", 0), reverse=True)
    for i, item in enumerate(items):
        item["rank"] = i + 1
    return items


def _compute_priority(value: int | float, effort: int | float) -> float:
    """WSJF-like priority = value / effort."""
    if effort <= 0:
        effort = 1
    return round(value / effort, 2)


# ═══════════════════════════════════════════════════════════════
#  BACKLOG COMMANDS
# ═══════════════════════════════════════════════════════════════


def _parse_flags(args: list[str]) -> dict[str, str]:
    """Parse --flag value pairs from args."""
    flags: dict[str, str] = {}
    skip = False
    for i, a in enumerate(args):
        if skip:
            skip = False
            continue
        if a.startswith("--"):
            key = a[2:]
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                flags[key] = args[i + 1]
                skip = True
            else:
                flags[key] = ""
    return flags


def backlog_init(backlog_dir: str) -> int:
    """Initialize an empty backlog."""
    backlog_dir = os.path.abspath(backlog_dir)
    path = _backlog_path(backlog_dir)

    if os.path.exists(path):
        eprint(f"⚠️  Ya existe un backlog en {backlog_dir}")
        items = _load_backlog(backlog_dir)
        print(f"   Items existentes: {len(items)}")
        return 0

    os.makedirs(backlog_dir, exist_ok=True)
    with open(path, "w") as f:
        f.write("")

    print()
    print("=" * 70)
    print(f"✅ BACKLOG INICIALIZADO: {backlog_dir}")
    print("=" * 70)
    print(f"   Ruta: {path}")
    print("   Items: 0")
    print()
    print("📋 Para agregar items:")
    print(
        f'   spring.py backlog add {backlog_dir} --title "Implementar login" --value 8 --effort 3'
    )
    print()
    print("📋 Para listar:")
    print(f"   spring.py backlog list {backlog_dir}")
    print("=" * 70)
    return 0


def backlog_add(
    backlog_dir: str,
    *,
    title: str,
    desc: str = "",
    value: int = 5,
    effort: int = 5,
    domain: str = "core",
    scrum_role: str = "",
    criteria: str = "[]",
    tags: str = "",
) -> int:
    """Add an item to the backlog."""
    if not title:
        eprint("❌ --title es requerido")
        return 2

    business_value = value
    effort_estimate = effort

    if business_value < 1 or business_value > 10:
        eprint("❌ value debe estar entre 1 y 10")
        return 2
    if effort_estimate < 1 or effort_estimate > 10:
        eprint("❌ effort debe estar entre 1 y 10")
        return 2

    try:
        acceptance_criteria = json.loads(criteria)
    except json.JSONDecodeError:
        acceptance_criteria = []
    if not isinstance(acceptance_criteria, list):
        acceptance_criteria = []

    items = _load_backlog(backlog_dir)
    item_id = _next_backlog_id(items)
    priority_score = _compute_priority(business_value, effort_estimate)

    item = {
        "id": item_id,
        "title": title,
        "description": desc,
        "business_value": business_value,
        "effort_estimate": effort_estimate,
        "priority_score": priority_score,
        "status": "backlog",
        "acceptance_criteria": acceptance_criteria,
        "domain": domain,
        "scrum_role": scrum_role if scrum_role in ("po", "dev", "qa") else None,
        "tags": [t.strip() for t in tags.split(",") if t.strip()] if tags else [],
        "created_at": _now_iso(),
        "refined_at": None,
    }

    _append_backlog(backlog_dir, item)

    print()
    print("=" * 70)
    print(f"✅ BACKLOG ITEM AGREGADO: {item_id}")
    print("=" * 70)
    print(f"  Title:       {title}")
    print(f"  Value:       {business_value}/10")
    print(f"  Effort:      {effort_estimate}/10")
    print(f"  Priority:    {priority_score} (WSJF)")
    print(f"  Domain:      {domain}")
    role_label = {"po": "Product Owner", "dev": "Developer", "qa": "QA Engineer"}.get(
        scrum_role, "—"
    )
    print(f"  Scrum Role:  {role_label}")
    print(f"  Backlog:     {backlog_dir}")
    print()
    print("📋 Para ver el backlog completo:")
    print(f"   spring.py backlog list {backlog_dir}")
    print("📋 Para seleccionar items para un sprint:")
    print(f"   spring.py backlog select {backlog_dir} {item_id} [mas IDs]")
    print("=" * 70)
    return 0


def backlog_list(backlog_dir: str, status: str = "") -> int:
    """List backlog items sorted by priority."""
    backlog_dir = os.path.abspath(backlog_dir)
    items = _load_backlog(backlog_dir)

    if not items:
        print(f"📭 Backlog vacío en {backlog_dir}")
        print(f'   Agrega items con: spring.py backlog add {backlog_dir} --title "..."')
        return 0

    if status:
        items = [it for it in items if it.get("status") == status]

    if not items:
        print(f"📭 No hay items con status '{status}'")
        return 0

    items = _prioritize(items)

    status_icons = {
        "backlog": "📋",
        "refined": "🔍",
        "in-sprint": "🔄",
        "done": "✅",
    }

    has_roles = any(it.get("scrum_role") for it in items)

    if has_roles:
        header = f"  {'Rank':<6} {'ID':<8} {'Prioridad':<11} {'Valor':<7} {'Esfuerzo':<9} {'Rol':<7} {'Status':<12} {'Title'}"
        sep = f"  {'─' * 5}  {'─' * 7}  {'─' * 10}  {'─' * 6}  {'─' * 8}  {'─' * 6}  {'─' * 10}  {'─' * 45}"
    else:
        header = f"  {'Rank':<6} {'ID':<8} {'Prioridad':<11} {'Valor':<7} {'Esfuerzo':<9} {'Status':<12} {'Title'}"
        sep = f"  {'─' * 5}  {'─' * 7}  {'─' * 10}  {'─' * 6}  {'─' * 8}  {'─' * 10}  {'─' * 50}"

    print("=" * 70)
    print(f"📋 PRODUCT BACKLOG ({len(items)} items)")
    print("=" * 70)
    print(header)
    print(sep)

    for item in items:
        rank = item.get("rank", "?")
        iid = item.get("id", "?")
        prio = item.get("priority_score", 0.0)
        val = item.get("business_value", "?")
        eff = item.get("effort_estimate", "?")
        role = item.get("scrum_role", "")
        role_icons = {"po": "🧑‍💼", "dev": "👨‍💻", "qa": "🔍"}
        role_icon = role_icons.get(role, "") if role else ""
        status = item.get("status", "backlog")
        icon = status_icons.get(status, "📋")
        title = item.get("title", "")[:65]
        if has_roles:
            print(
                f"  #{rank:<3}  {iid:<7}  {prio:<8.2f}  {val}/10{'':<2}  {eff}/10{'':<4}  {role_icon:<6}  {icon} {status:<8}  {title}"
            )
        else:
            print(
                f"  #{rank:<3}  {iid:<7}  {prio:<8.2f}  {val}/10{'':<2}  {eff}/10{'':<4}  {icon} {status:<8}  {title}"
            )

    print("─" * 70)
    if not status:
        counts: dict[str, int] = {}
        for it in _load_backlog(backlog_dir):
            s = it.get("status", "backlog")
            counts[s] = counts.get(s, 0) + 1
        if counts:
            print(
                f"  📊 Resumen: {', '.join(f'{k}={v}' for k, v in sorted(counts.items()))}"
            )
    print()
    print("📋 Para refinar un item:")
    print(
        f'   spring.py backlog refine {backlog_dir} <item_id> --field title "nuevo titulo"'
    )
    print("📋 Para seleccionar items para un sprint:")
    print(f"   spring.py backlog select {backlog_dir} <item_ids...>")
    print("=" * 70)
    return 0


def backlog_prioritize(backlog_dir: str, item_id: str, new_rank: int) -> int:
    """Reorder a backlog item by changing its priority score."""
    if new_rank < 1:
        eprint("❌ new_rank debe ser >= 1")
        return 2

    backlog_dir = os.path.abspath(backlog_dir)
    items = _load_backlog(backlog_dir)

    target = None
    target_idx = None
    for i, it in enumerate(items):
        if it.get("id") == item_id:
            target = it
            target_idx = i
            break

    if target is None:
        eprint(f"❌ Item {item_id} no encontrado en {backlog_dir}")
        return 2

    items.pop(target_idx)

    if new_rank > len(items) + 1:
        new_rank = len(items) + 1

    items = _prioritize(items)
    items.insert(new_rank - 1, target)
    for i, it in enumerate(items):
        it["priority_score"] = (len(items) - i) * 10

    _save_backlog(backlog_dir, _prioritize(items))

    print()
    print("=" * 70)
    print(f"✅ ITEM REORDENADO: {item_id} → rank #{new_rank}")
    print("=" * 70)
    print(f"   Backlog: {backlog_dir}")
    print(f"   Title:   {target.get('title', '')[:80]}")
    print()
    print("📋 Para ver el backlog:")
    print(f"   spring.py backlog list {backlog_dir}")
    print("=" * 70)
    return 0


_FIELD_MAP = {
    "title": "title",
    "desc": "description",
    "description": "description",
    "value": "business_value",
    "effort": "effort_estimate",
    "domain": "domain",
    "status": "status",
    "tags": "tags",
    "criteria": "acceptance_criteria",
    "scrum-role": "scrum_role",
    "scrum_role": "scrum_role",
}


def backlog_refine(backlog_dir: str, item_id: str, field: str, value: str) -> int:
    """Edit a field of a backlog item."""
    backlog_dir = os.path.abspath(backlog_dir)

    internal_key = _FIELD_MAP.get(field)
    if internal_key is None:
        eprint(f"❌ Campo desconocido: {field}")
        eprint("   Campos: title, desc, value, effort, domain, status, tags, criteria")
        return 2

    items = _load_backlog(backlog_dir)
    target = None
    for it in items:
        if it.get("id") == item_id:
            target = it
            break

    if target is None:
        eprint(f"❌ Item {item_id} no encontrado en {backlog_dir}")
        return 2

    old_value = target.get(internal_key)

    if field in ("value", "effort"):
        try:
            new_val = int(value)
        except ValueError:
            eprint(f"❌ {field} debe ser un número entero")
            return 2
        if new_val < 1:
            eprint(f"❌ {field} debe ser >= 1")
            return 2
        target[internal_key] = new_val
        bv = target.get("business_value", 5)
        ef = target.get("effort_estimate", 5)
        target["priority_score"] = _compute_priority(bv, ef)
    elif field == "criteria":
        try:
            new_val = json.loads(value)
        except json.JSONDecodeError:
            new_val = [value]
        if not isinstance(new_val, list):
            new_val = [str(new_val)]
        target[internal_key] = new_val
    elif field == "tags":
        target[internal_key] = [t.strip() for t in value.split(",") if t.strip()]
    elif field == "status":
        allowed = ("backlog", "refined", "in-sprint", "done")
        if value not in allowed:
            eprint(f"❌ Status debe ser uno de: {', '.join(allowed)}")
            return 2
        target[internal_key] = value
        if value == "refined":
            target["refined_at"] = _now_iso()
    else:
        target[internal_key] = value

    target["refined_at"] = _now_iso()
    if target.get("status") == "backlog":
        target["status"] = "refined"

    _save_backlog(backlog_dir, _prioritize(items))

    print()
    print("=" * 70)
    print(f"✅ ITEM REFINADO: {item_id}")
    print("=" * 70)
    print(f"  Campo editado: {field} → {str(value)[:60]}")
    if old_value is not None:
        old_str = str(old_value)[:60]
        print(f"  Valor anterior: {old_str}")
    print(f"  Backlog: {backlog_dir}")
    print(f"  Title:   {target.get('title', '')[:80]}")
    print()
    print("📋 Para ver el backlog:")
    print(f"   spring.py backlog list {backlog_dir}")
    print("📋 Para usar en planning:")
    print(
        f"   spring.py planning propose <chain> --backlog-dir {backlog_dir} --item-ids {item_id}"
    )
    print("=" * 70)
    return 0


def backlog_select(backlog_dir: str, item_ids: list[str]) -> int:
    """Select items for a sprint (mark them as 'in-sprint')."""
    backlog_dir = os.path.abspath(backlog_dir)
    items = _load_backlog(backlog_dir)

    selected = []
    not_found = []
    already_in_sprint = []

    for iid in item_ids:
        found = False
        for it in items:
            if it.get("id") == iid:
                found = True
                if it.get("status") == "in-sprint":
                    already_in_sprint.append(iid)
                else:
                    it["status"] = "in-sprint"
                    it["refined_at"] = _now_iso()
                    selected.append(it)
                break
        if not found:
            not_found.append(iid)

    if selected:
        _save_backlog(backlog_dir, _prioritize(items))

    print()
    print("=" * 70)
    print(f"✅ ITEMS SELECCIONADOS ({len(selected)} items)")
    print("=" * 70)

    if selected:
        for item in selected:
            prio = item.get("priority_score", 0)
            print(f"  ✅ {item['id']} (prioridad {prio}): {item.get('title', '')[:70]}")
    if already_in_sprint:
        print(f"  ⚠️  Ya en sprint: {', '.join(already_in_sprint)}")
    if not_found:
        print(f"  ❌ No encontrados: {', '.join(not_found)}")

    print(f"  Backlog: {backlog_dir}")
    print()
    print("📋 Para usar en Sprint Planning:")
    ids_str = " ".join(it.get("id", "") for it in selected)
    print(
        f"   spring.py planning propose <chain> --backlog-dir {backlog_dir} --item-ids {ids_str}"
    )
    print("=" * 70)
    return 0


def cmd_backlog(args: list[str]) -> int:
    """Dispatch backlog subcommands: init, add, list, prioritize, refine, select."""
    if not args:
        eprint("❌ Uso: spring.py backlog <subcomando> [args...]")
        eprint("   Subcomandos: init, add, list, prioritize, refine, select")
        eprint("   spring.py backlog --help para detalle")
        return 2

    subcommand = args[0]
    sub_args = args[1:]

    if subcommand == "init":
        if not sub_args:
            eprint("❌ Uso: spring.py backlog init <backlog_dir>")
            return 2
        return backlog_init(sub_args[0])
    elif subcommand == "add":
        if not sub_args:
            eprint('❌ Uso: spring.py backlog add <backlog_dir> --title "..." [flags]')
            return 2
        flags = _parse_flags(sub_args[1:])
        try:
            value = int(flags.get("value", "5"))
        except ValueError:
            eprint("❌ --value debe ser un número entero (1-10)")
            return 2
        try:
            effort = int(flags.get("effort", "5"))
        except ValueError:
            eprint("❌ --effort debe ser un número entero (1-10)")
            return 2
        return backlog_add(
            sub_args[0],
            title=flags.get("title", ""),
            desc=flags.get("desc", ""),
            value=value,
            effort=effort,
            domain=flags.get("domain", "core"),
            scrum_role=flags.get("scrum-role", ""),
            criteria=flags.get("criteria", "[]"),
            tags=flags.get("tags", ""),
        )
    elif subcommand == "list":
        if not sub_args:
            eprint(
                "❌ Uso: spring.py backlog list <backlog_dir> [--status backlog|refined|in-sprint|done]"
            )
            return 2
        flags = _parse_flags(sub_args[1:])
        return backlog_list(sub_args[0], status=flags.get("status", ""))
    elif subcommand == "prioritize":
        if len(sub_args) < 3:
            eprint(
                "❌ Uso: spring.py backlog prioritize <backlog_dir> <item_id> <new_rank>"
            )
            return 2
        try:
            new_rank = int(sub_args[2])
        except ValueError:
            eprint("❌ new_rank debe ser un número entero positivo")
            return 2
        return backlog_prioritize(sub_args[0], sub_args[1], new_rank)
    elif subcommand == "refine":
        if len(sub_args) < 2:
            eprint(
                "❌ Uso: spring.py backlog refine <backlog_dir> <item_id> --field <campo> <valor>"
            )
            return 2
        field = ""
        value = ""
        for i, a in enumerate(sub_args):
            if a == "--field" and i + 2 < len(sub_args):
                field = sub_args[i + 1]
                value = sub_args[i + 2]
                break
        if not field or not value:
            eprint("❌ --field <campo> <valor> es requerido")
            return 2
        return backlog_refine(sub_args[0], sub_args[1], field, value)
    elif subcommand == "select":
        if len(sub_args) < 2:
            eprint("❌ Uso: spring.py backlog select <backlog_dir> <item_ids...>")
            return 2
        return backlog_select(sub_args[0], sub_args[1:])
    elif subcommand in ("--help", "-h"):
        print("spring.py backlog — Gestion del Product Backlog")
        print()
        print("  init <backlog_dir>")
        print("    Inicializa un backlog vacio en el directorio.")
        print()
        print(
            '  add <backlog_dir> --title "..." [--desc "..."] [--value N] [--effort N] [--domain X] [--criteria \'[...]\']'
        )
        print("    Agrega un item al backlog.")
        print("    --value: valor de negocio (1-10, default 5)")
        print("    --effort: esfuerzo estimado (1-10, default 5)")
        print()
        print("  list <backlog_dir> [--status backlog|refined|in-sprint|done]")
        print("    Lista items ordenados por prioridad (WSJF).")
        print()
        print("  prioritize <backlog_dir> <item_id> <new_rank>")
        print("    Reordena un item en el backlog.")
        print()
        print("  refine <backlog_dir> <item_id> --field <campo> <valor>")
        print("    Edita un campo del item.")
        print()
        print("  select <backlog_dir> <item_ids...>")
        print("    Exporta items al planning (marca 'in-sprint').")
        print()
        print(
            "  La prioridad se calcula como valor_de_negocio / esfuerzo_estimado (WSJF)."
        )
        return 0
    else:
        eprint(f"❌ Subcomando desconocido: {subcommand}")
        eprint("   Subcomandos: init, add, list, prioritize, refine, select")
        return 2


# ═══════════════════════════════════════════════════════════════
#  SPRINT PLANNING COMMANDS
# ═══════════════════════════════════════════════════════════════

# ── Schema-driven task validation (pre-write) ───────────────────
# Los campos requeridos siguen el schema vigente (plan.schema.json $defs/task).
_TASK_REQUIRED_FIELDS = [
    "task_id",
    "type",
    "objective",
    "result_state",
    "task_description",
    "domain",
    "impacted_artifacts",
    "risks",
    "task_context",
    "validation",
    "role",
    "scrum_role",
    "work_type",
    "importer_files",
    "depends_on",
    "preconditions",
    "acceptance_criteria",
    "quality_gates",
    "loop_verification",
    "loop_acceptance",
    "decisions",
    "refinement",
]

_TASK_FIELD_DESCRIPTIONS = {
    "type": "string — feature|fix|refactor|test|docs",
    "domain": "string — modulo del proyecto (ej: auth, core, api)",
    "impacted_artifacts": 'object con {"files": ["ruta/archivo.py"]}',
    "risks": 'object con {"curvature": 0.0-1.0, "commit_trust": 0.0-1.0}',
    "task_context": "object con 6 capas de analisis",
    "validation": 'object con {"predicates": ["..."]}',
}


def _task_defaults(t: dict) -> dict:
    """Rellena campos requeridos por schema con defaults (reemplaza None)."""
    result = dict(t)
    for field, default in {
        "role": "core",
        "scrum_role": "dev",
        "work_type": "refactor",
        "importer_files": 0,
        "depends_on": [],
        "preconditions": [],
        "quality_gates": [],
        "loop_verification": [],
        "loop_acceptance": "all",
        "decisions": [],
        "refinement": {"attempts": 0, "dead_ends": []},
        "task_description": (t.get("objective") or "")[:100],
        "acceptance_criteria": [t["result_state"]] if t.get("result_state") else [],
    }.items():
        if field not in result or result[field] is None:
            result[field] = default
    # files no vacío (requerido por schema vigente)
    files = result.get("impacted_artifacts", {}).get("files", [])
    if not files:
        result["impacted_artifacts"] = {"files": ["src/main.py"]}
    return result


def _validate_task_fields(tasks: list[dict]) -> tuple[bool, list[str]]:
    """Pre-write validation: check each task has all required schema fields."""
    errors: list[str] = []
    for i, t in enumerate(tasks):
        tid = t.get("task_id", f"#{i + 1}")
        for field in _TASK_REQUIRED_FIELDS:
            if field not in t:
                desc = _TASK_FIELD_DESCRIPTIONS.get(field, "")
                hint = f" — {desc}" if desc else ""
                errors.append(
                    f"  ❌ Tarea {tid}: falta campo requerido '{field}'{hint}"
                )
            elif t[field] is None:
                errors.append(
                    f"  ❌ Tarea {tid}: campo '{field}' es null (debe tener valor)"
                )
    return len(errors) == 0, errors


def _add_task_context_to_tasks(tasks: list[dict], goal: str) -> list[dict]:
    """Add task_context to each task (required by plan.schema.json)."""
    result = []
    for t in tasks:
        obj = t.get("objective", goal)[:100]
        files = t.get("impacted_artifacts", {}).get("files", [])
        file_list = ", ".join(files) if files else "por determinar"
        domain = t.get("domain", "core")

        t["task_context"] = {
            "observation": f"Objetivo: {obj}. Archivos afectados: {file_list}. Dominio: {domain}.",
            "structure": f"Los archivos {file_list} estan en el dominio {domain}. Dependencias a investigar.",
            "behavior": f"La implementacion debe cumplir: {obj}. Flujo a definir durante la implementacion.",
            "causality": "No hay restricciones conocidas. Investigar durante el desarrollo.",
            "purpose": f"Cumplir con el goal del sprint: {goal[:80]}.",
            "principles": f"Seguir las convenciones del proyecto en {domain}.",
        }
        t = _task_defaults(t)
        result.append(t)
    return result


def _proposal_path(chain_dir: str) -> str:
    return os.path.join(chain_dir, ".planning", "proposal.json")


def _resolve_project_root(project_id: str, chain_dir: str) -> str:
    """Resuelve el directorio cognitivo del proyecto (proyecto activo del chain_dir).

    Preferencia:
      1. ProjectService.resolve_paths(chain_dir) → project_root del proyecto
         que contiene al sprint (mismo rail que usa SpringService.from_chain_dir).
      2. Fallback: get_project_dir(project_id) si el proyecto está registrado.
      3. Fallback último: padre del delta desde chain_dir.
    """
    try:
        # NOTE: ProjectService import moved inside function to break cycle
        # features.delta_tracking -> features.project (detected by Tarjan gate).
        # Resolved dynamically at runtime via module service locator.
        from core.engine.bootstrap import resolve_service

        project_service = resolve_service("project_service")
        if project_service is None:
            return ""

        paths = project_service.resolve_paths(chain_dir)
        data_dir = getattr(paths, "data_dir", "") or ""
        root = os.path.dirname(data_dir) if data_dir else ""
        if root and os.path.isdir(root):
            return root
    except Exception:
        pass
    if project_id:
        try:
            from shared.core.path_resolver import get_project_dir

            root = str(get_project_dir(project_id))
            if root and os.path.isdir(root):
                return root
        except Exception:
            pass
    parent = os.path.dirname(os.path.abspath(chain_dir))
    return parent if os.path.isdir(parent) else ""


def _load_proposal(chain_dir: str) -> dict | None:
    return load_json(_proposal_path(chain_dir))


def planning_propose(
    chain_dir: str,
    *,
    project_id: str,
    goal: str,
    backlog_dir: str = "",
    item_ids_raw: str = "",
    tasks_json: str = "[]",
    predicates_json: str = "{}",
    scrum_role_default: str = "",
) -> int:
    """Propose a sprint plan (saves to .planning/proposal.json and blocks)."""
    chain_dir = os.path.abspath(chain_dir)

    if not project_id:
        eprint("❌ --project-id es requerido")
        return 2
    if not goal:
        eprint("❌ --goal es requerido")
        return 2
    if len(goal) < 10:
        eprint("❌ --goal debe tener al menos 10 caracteres")
        return 2

    try:
        tasks = json.loads(tasks_json)
    except json.JSONDecodeError as e:
        eprint(f"❌ tasks JSON inválido: {e}")
        return 2
    try:
        predicates = json.loads(predicates_json)
    except json.JSONDecodeError:
        predicates = {"must_exist": [], "must_not_exist": [], "invariants": []}

    # Normalize predicates
    predicates = _normalize_predicates(predicates)

    # Load backlog items if specified
    backlog_items = _load_backlog_items(backlog_dir, item_ids_raw)

    # Validate tasks / generate from backlog o desde predicates (síntesis)
    has_predicates = any(
        predicates.get(k) for k in ("must_exist", "must_not_exist", "invariants")
    )
    if not tasks and has_predicates:
        # Síntesis tipada desde predicates (feature/bugfix/test + files + riesgo)
        known_files = collect_known_files(
            [it.get("files", []) for it in backlog_items], []
        )
        from features.delta_tracking.application.task_synthesis import (
            generate_tasks_from_predicates,
            fill_task_contexts,
        )

        tasks = generate_tasks_from_predicates(
            predicates, project_id, known_files=known_files
        )
        fill_task_contexts(tasks, goal, project_id)
    elif not tasks:
        tasks = _generate_tasks_from_backlog(tasks, backlog_items, scrum_role_default)
        if not tasks:
            eprint("❌ No hay tareas definidas ni backlog items seleccionados.")
            eprint("   Pasa --tasks '[...]', --backlog-dir y --item-ids,")
            eprint("   o --predicates con must_exist/must_not_exist/invariants")
            return 2

    # Enriquecer tasks con predicates (type/files/riesgo derivados)
    if has_predicates:
        known_files = collect_known_files(
            [it.get("files", []) for it in backlog_items], []
        )
        enrich_tasks_with_predicates(tasks, predicates, known_files=known_files)

    # Validate each task
    if not _validate_tasks_min_length(tasks):
        return 2

    # PRE-WRITE VALIDATION
    tasks = _add_task_context_to_tasks(tasks, goal)
    valid, field_errors = _validate_task_fields(tasks)
    if not valid:
        eprint()
        eprint("=" * 70)
        eprint("❌ VALIDACIÓN PRE-WRITE: Faltan campos requeridos en las tareas")
        eprint("=" * 70)
        for err in field_errors:
            eprint(err)
        eprint()
        eprint(
            "  Referencia: features/delta_tracking/schema/plan.schema.json → $defs/task → required"
        )
        eprint()
        eprint("  Corrige las tareas y reintenta con --tasks:")
        eprint("=" * 70)
        return 2

    # Normalize predicates
    predicates = _normalize_predicates(predicates)

    now = _now_iso()
    chain_id = sanitize_chain_id(os.path.basename(os.path.normpath(chain_dir)))

    proposal = {
        "meta": {
            "chain_id": chain_id,
            "project_id": project_id,
            "goal": goal,
            "generated_at": now,
            "status": "proposed",
            "backlog_source": os.path.abspath(backlog_dir) if backlog_dir else None,
            "backlog_items": [it.get("id") for it in backlog_items],
        },
        "goal": {
            "description": goal,
            "predicates": predicates,
            "signatures": [],
        },
        "tasks": tasks,
        "backlog_items": backlog_items,
    }

    planning_dir = os.path.join(chain_dir, ".planning")
    os.makedirs(planning_dir, exist_ok=True)
    proposal_path = _proposal_path(chain_dir)
    write_json(proposal_path, proposal)

    _print_proposal(
        chain_id, project_id, goal, tasks, predicates, backlog_items, proposal_path
    )

    return 3


def _load_backlog_items(backlog_dir: str, item_ids_raw: str) -> list[dict]:
    """Carga los backlog items seleccionados por id."""
    backlog_items = []
    if backlog_dir and item_ids_raw:
        item_ids = [i.strip() for i in item_ids_raw.split(",") if i.strip()]
        items = _load_backlog(os.path.abspath(backlog_dir))
        for iid in item_ids:
            for it in items:
                if it.get("id") == iid:
                    backlog_items.append(it)
                    break
    return backlog_items


def _generate_tasks_from_backlog(
    tasks: list[dict], backlog_items: list[dict], scrum_role_default: str
) -> list[dict]:
    """Genera tareas desde los backlog items."""
    for item in backlog_items:
        tid = f"TASK-{len(tasks) + 1:03d}"
        item_role = item.get("scrum_role") or scrum_role_default or None
        tasks.append(
            {
                "task_id": tid,
                "type": "feature",
                "scrum_role": item_role,
                "domain": item.get("domain", "core"),
                "objective": item.get("title", ""),
                "result_state": (item.get("description") or item.get("title", ""))[
                    :200
                ],
                "impacted_artifacts": {"files": []},
                "risks": {"curvature": 0.20},
                "validation": {"predicates": []},
                "acceptance_criteria": item.get("acceptance_criteria", []),
            }
        )
    return tasks


def _validate_tasks_min_length(tasks: list[dict]) -> bool:
    """Valida que objective/result_state tengan >= 10 caracteres."""
    for t in tasks:
        obj = t.get("objective", "")
        rs = t.get("result_state", "")
        if len(obj) < 10:
            eprint(
                f"❌ Tarea {t.get('task_id', '?')}: objective debe tener >= 10 caracteres"
            )
            return False
        if len(rs) < 10:
            eprint(
                f"❌ Tarea {t.get('task_id', '?')}: result_state debe tener >= 10 caracteres"
            )
            return False
    return True


def _normalize_predicates(predicates) -> dict:
    """Normaliza predicates a dict {must_exist, must_not_exist, invariants}."""
    if isinstance(predicates, list):
        return {
            "must_exist": [p for p in predicates if not p.startswith("NO ")],
            "must_not_exist": [p[3:] for p in predicates if p.startswith("NO ")],
            "invariants": [],
        }
    if isinstance(predicates, dict):
        predicates.setdefault("must_exist", [])
        predicates.setdefault("must_not_exist", [])
        predicates.setdefault("invariants", [])
    return predicates


def _print_proposal(
    chain_id: str,
    project_id: str,
    goal: str,
    tasks: list[dict],
    predicates: dict,
    backlog_items: list[dict],
    proposal_path: str,
) -> None:
    """Imprime el resumen de la propuesta de sprint."""
    print()
    print("=" * 70)
    print(f"📋 SPRINT PLAN PROPUESTO: {chain_id}")
    print("=" * 70)
    print(f"  Proyecto:    {project_id}")
    print(f"  Goal:        {goal[:80]}")
    print(f"  Tareas:      {len(tasks)}")
    print(
        f"  Predicates:  {len(predicates.get('must_exist', []))} must_exist, "
        f"{len(predicates.get('must_not_exist', []))} must_not_exist, "
        f"{len(predicates.get('invariants', []))} invariants"
    )
    if backlog_items:
        print(f"  Backlog:     {len(backlog_items)} items seleccionados")
    print(f"  Path:        {proposal_path}")
    print()
    print("📋 TAREAS PROPUESTAS:")
    for i, t in enumerate(tasks):
        obj = t.get("objective", "")[:70]
        domain = t.get("domain", "?")
        print(f"  {i + 1}. {t.get('task_id', '?')} [{domain}] {obj}")

    print()
    print("📋 DECISION PROTOCOL:")
    print()
    print("  ▶️  Para REFINAR el plan (cambiar goal, tareas, predicates):")
    print(f'     spring.py planning refine {chain_id} --edit "campo=valor"')
    print()
    print("  ▶️  Para CONFIRMAR y crear el sprint:")
    print(f"     spring.py planning commit {chain_id}")
    print()
    print(
        "⚠️  El plan está en .planning/proposal.json. Usa commit para hacerlo efectivo."
    )
    print("=" * 70)


def planning_refine(chain_dir: str, edit_value: str) -> int:
    """Edit a field in the proposed sprint plan."""
    chain_dir = os.path.abspath(chain_dir)
    proposal_path = _proposal_path(chain_dir)

    proposal = _load_proposal(chain_dir)
    if proposal is None:
        eprint(f"❌ No hay propuesta en {chain_dir}. Ejecuta propose primero.")
        return 2

    if proposal.get("meta", {}).get("status") != "proposed":
        eprint(
            f"❌ La propuesta ya fue procesada (status: {proposal.get('meta', {}).get('status')})"
        )
        return 2

    if not edit_value:
        eprint('❌ --edit "campo=valor" es requerido')
        return 2

    parts = edit_value.split("=", 1)
    if len(parts) != 2:
        eprint('❌ Formato: --edit "campo=valor"')
        return 2

    field = parts[0].strip()
    value = parts[1].strip().strip('"').strip("'")

    if not field or not value:
        eprint("❌ Campo y valor no pueden estar vacíos")
        return 2

    if field == "goal":
        if len(value) < 10:
            eprint("❌ goal debe tener al menos 10 caracteres")
            return 2
        proposal["meta"]["goal"] = value
        proposal["goal"]["description"] = value
        print(f"✅ Goal actualizado: {value[:80]}")

    elif field == "project-id":
        proposal["meta"]["project_id"] = value
        print(f"✅ Project ID actualizado: {value}")

    elif field.startswith("tasks"):
        sub_parts = field.split(":", 1)
        if len(sub_parts) == 2:
            sub_cmd = sub_parts[1]
            if sub_cmd == "add":
                try:
                    new_task = json.loads(value)
                except json.JSONDecodeError:
                    eprint("❌ tasks:add requiere JSON de tarea")
                    return 2

                goal = proposal.get("meta", {}).get("goal", "")
                enriched = _add_task_context_to_tasks([new_task], goal)
                valid, errors = _validate_task_fields(enriched)
                if not valid:
                    eprint()
                    eprint("❌ VALIDACIÓN: La tarea nueva no cumple el schema")
                    for e in errors:
                        eprint(e)
                    eprint()
                    eprint(
                        "  Referencia: features/delta_tracking/schema/plan.schema.json → $defs/task → required"
                    )
                    eprint("=" * 70)
                    return 2

                tasks = proposal.get("tasks", [])
                new_id = f"TASK-{len(tasks) + 1:03d}"
                new_task["task_id"] = new_id
                tasks.append(new_task)
                print(
                    f"✅ Tarea agregada: {new_id} — {new_task.get('objective', '')[:60]}"
                )
            elif sub_cmd == "remove":
                tasks = proposal.get("tasks", [])
                tasks = [t for t in tasks if t.get("task_id") != value]
                proposal["tasks"] = tasks
                print(f"✅ Tarea eliminada: {value}")
            else:
                eprint(f"❌ Subcomando desconocido: tasks:{sub_cmd}")
                return 2
        else:
            try:
                new_tasks = json.loads(value)
            except json.JSONDecodeError:
                eprint("❌ tasks requiere JSON array")
                return 2

            goal = proposal.get("meta", {}).get("goal", "")
            enriched = _add_task_context_to_tasks(new_tasks, goal)
            valid, errors = _validate_task_fields(enriched)
            if not valid:
                eprint()
                eprint("❌ VALIDACIÓN: Las nuevas tareas no cumplen el schema")
                for e in errors:
                    eprint(e)
                eprint(
                    "  Referencia: features/delta_tracking/schema/plan.schema.json → $defs/task → required"
                )
                eprint("=" * 70)
                return 2

            proposal["tasks"] = new_tasks
            print(f"✅ Tasks actualizadas: {len(new_tasks)} tareas")

    elif field.startswith("predicates"):
        try:
            new_preds = json.loads(value)
        except json.JSONDecodeError:
            eprint("❌ predicates requiere JSON")
            return 2
        proposal["goal"]["predicates"] = new_preds
        preds = new_preds
        print(
            f"✅ Predicates actualizados: {len(preds.get('must_exist', []))} must_exist"
        )

    else:
        eprint(f"❌ Campo desconocido: {field}")
        eprint(
            "   Campos editables: goal, project-id, tasks:add, tasks:remove, predicates"
        )
        return 2

    write_json(proposal_path, proposal)

    tasks = proposal.get("tasks", [])
    g_obj = proposal.get("goal", {})
    preds = g_obj.get("predicates", {})

    print()
    print("📋 PLAN ACTUALIZADO:")
    print(f"  Goal:       {proposal['meta']['goal'][:80]}")
    print(f"  Proyecto:   {proposal['meta']['project_id']}")
    print(f"  Tareas:     {len(tasks)}")
    print(
        f"  Predicates: {len(preds.get('must_exist', []))} must_exist, "
        f"{len(preds.get('must_not_exist', []))} must_not_exist"
    )
    print()
    print("📋 Para confirmar el plan:")
    print(f"   spring.py planning commit {chain_dir}")
    print("📋 Para seguir refinando:")
    print(f'   spring.py planning refine {chain_dir} --edit "campo=valor"')

    return 0


def planning_commit(chain_dir: str) -> int:
    """Commit the planning proposal into the DELTA (fuente unica).

    PRINCIPIO: plan.json no existe como fuente paralela (ADR-WALKER-013).
    El spring + specs se crean en el DeltaGraph via SpringService.
    progress.json/plan.json NO se escriben.
    """
    chain_dir = os.path.abspath(chain_dir)
    proposal_path = _proposal_path(chain_dir)

    proposal = _load_proposal(chain_dir)
    if proposal is None:
        eprint(f"❌ No hay propuesta en {chain_dir}. Ejecuta propose primero.")
        return 2

    if proposal.get("meta", {}).get("status") != "proposed":
        eprint(
            f"❌ La propuesta ya fue procesada (status: {proposal.get('meta', {}).get('status')})"
        )
        return 2

    chain_id = proposal["meta"]["chain_id"]
    project_id = proposal["meta"]["project_id"]
    goal = proposal["meta"]["goal"]
    tasks = proposal.get("tasks", [])
    g_obj = proposal.get("goal", {})
    predicates = g_obj.get("predicates", {})
    now = _now_iso()

    if not tasks:
        eprint("❌ No hay tareas definidas. Refina el plan con tasks:add.")
        return 2

    tasks = _add_task_context_to_tasks(tasks, goal)

    valid, field_errors = _validate_task_fields(tasks)
    if not valid:
        eprint()
        eprint("=" * 70)
        eprint("❌ VALIDACIÓN PRE-WRITE: Faltan campos requeridos en las tareas")
        eprint("=" * 70)
        for err in field_errors:
            eprint(err)
        eprint()
        eprint(
            "  Referencia: features/delta_tracking/schema/plan.schema.json → $defs/task → required"
        )
        eprint("=" * 70)
        return 2

    # ── Persistir en el DELTA (fuente unica) ─────────────────────
    from features.delta_tracking import SpringService

    project_root = _resolve_project_root(project_id, chain_dir)
    spring_svc = SpringService(project_root)
    spring_svc.create_spring(
        chain_id,
        goal=goal,
        status="planning",
        project_id=project_id,
    )
    spring_svc.upsert_specs_batch(
        chain_id,
        [
            {
                "objective": t.get("objective", ""),
                "domain": t.get("domain", ""),
                "type": t.get("type", "feature"),
                "result_state": t.get("result_state", ""),
                "files": (
                    t.get("impacted_artifacts", {}).get("files", [])
                    if isinstance(t.get("impacted_artifacts"), dict)
                    else []
                ),
                "status": "draft",
                "description": t.get("task_description", ""),
            }
            for t in tasks
        ],
    )

    # ── Update proposal status ────────────────────────────────
    proposal["meta"]["status"] = "committed"
    proposal["meta"]["committed_at"] = now
    write_json(proposal_path, proposal)

    # ── Update backlog items status ───────────────────────────
    backlog_dir = proposal["meta"].get("backlog_source")
    backlog_items = proposal.get("backlog_items", [])
    if backlog_dir and backlog_items:
        abs_backlog = backlog_dir
        if not os.path.isdir(abs_backlog):
            # fallback: reconstrucción relativa al padre del chain_dir
            abs_backlog = os.path.join(
                os.path.dirname(os.path.dirname(chain_dir)), backlog_dir
            )
        if os.path.isdir(abs_backlog):
            all_items = _load_backlog(abs_backlog)
            selected_ids = [it.get("id") for it in backlog_items]
            for it in all_items:
                if it.get("id") in selected_ids and it.get("status") != "in-sprint":
                    it["status"] = "in-sprint"
                    it["sprint"] = chain_id
            _save_backlog(abs_backlog, _prioritize(all_items))

    # ── Output ────────────────────────────────────────────────
    print()
    print("=" * 70)
    print(f"✅ SPRINT CREADO DESDE PLANNING: {chain_id}")
    print("=" * 70)
    print(f"  Proyecto:    {project_id}")
    print(f"  Goal:        {goal[:80]}")
    print(f"  Tareas:      {len(tasks)}")
    print(
        f"  Predicates:  {len(predicates.get('must_exist', []))} must_exist, "
        f"{len(predicates.get('must_not_exist', []))} must_not_exist, "
        f"{len(predicates.get('invariants', []))} invariants"
    )
    print()
    print("📋 DECISION PROTOCOL:")
    print()
    print("  ▶️  Para ver estado:")
    print(f"     bin/walker3 status {chain_dir}")
    print()
    print("⚠️  Importante: task.py advance ahora requiere --confirm primero")
    print("    y luego --human-confirm (solo el usuario) para completar tareas.")
    print("=" * 70)

    return 0


def cmd_planning(args: list[str]) -> int:
    """Dispatch planning subcommands: propose, refine, commit."""
    if not args:
        eprint("❌ Uso: spring.py planning <subcomando> [args...]")
        eprint("   Subcomandos: propose, refine, commit")
        eprint("   spring.py planning --help para detalle")
        return 2

    subcommand = args[0]
    sub_args = args[1:]

    if subcommand == "propose":
        if not sub_args:
            eprint(
                '❌ Uso: spring.py planning propose <chain_dir> --project-id X --goal "..."'
            )
            return 2
        flags = _parse_flags(sub_args[1:])
        return planning_propose(
            sub_args[0],
            project_id=flags.get("project-id", ""),
            goal=flags.get("goal", ""),
            backlog_dir=flags.get("backlog-dir", ""),
            item_ids_raw=flags.get("item-ids", ""),
            tasks_json=flags.get("tasks", "[]"),
            predicates_json=flags.get("predicates", "{}"),
            scrum_role_default=flags.get("scrum-role", ""),
        )
    elif subcommand == "refine":
        if not sub_args:
            eprint('❌ Uso: spring.py planning refine <chain_dir> --edit "campo=valor"')
            return 2
        edit_value = ""
        for i, a in enumerate(sub_args):
            if a == "--edit" and i + 1 < len(sub_args):
                edit_value = sub_args[i + 1]
                break
            if a.startswith("--edit="):
                edit_value = a[7:]
                break
        return planning_refine(sub_args[0], edit_value)
    elif subcommand == "commit":
        if not sub_args:
            eprint("❌ Uso: spring.py planning commit <chain_dir>")
            return 2
        return planning_commit(sub_args[0])
    elif subcommand in ("--help", "-h"):
        print("spring.py planning — Sprint Planning Ceremony")
        print()
        print(
            '  propose <chain_dir> --project-id X --goal "..." [--backlog-dir DIR] [--item-ids IDs]'
        )
        print("    Propone un plan de sprint guardado en .planning/proposal.json.")
        print("    Exit 3: plan propuesto, espera refine/commit del usuario.")
        print()
        print('  refine <chain_dir> --edit "campo=valor"')
        print("    Edita la propuesta: goal, tasks, predicates.")
        print("    Exit 0: propuesta actualizada.")
        print()
        print("  commit <chain_dir>")
        print("    Crea el sprint en el DELTA (SpringService — fuente unica).")
        print("    plan.json no se escribe (ADR-WALKER-013).")
        print("    Exit 0: sprint creado. Exit 2: error de validacion.")
        print()
        print("  El flujo completo: propose → refine (opcional) → commit")
        return 0
    else:
        eprint(f"❌ Subcomando desconocido: {subcommand}")
        eprint("   Subcomandos: propose, refine, commit")
        return 2


__all__ = [
    "cmd_backlog",
    "cmd_planning",
    "backlog_init",
    "backlog_add",
    "backlog_list",
    "backlog_prioritize",
    "backlog_refine",
    "backlog_select",
    "planning_propose",
    "planning_refine",
    "planning_commit",
    "_next_backlog_id",
    "_compute_priority",
    "_prioritize",
    "_load_backlog",
    "_save_backlog",
]
