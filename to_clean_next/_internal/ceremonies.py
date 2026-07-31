#!/usr/bin/env python3
"""
ceremonies.py — Scrum ceremonies for the delta skill.

Backlog management + Sprint Planning ceremony + Human Confirm gate.

Backlog items are stored as JSONL in <project>/delta/backlog.jsonl.

Planning proposals are stored in <chain_dir>/.planning/proposal.json.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone


# ── Helpers ────────────────────────────────────────────────────


def eprint(*args, **kwargs):
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
    # Add integrity checksum for progress.json
    if os.path.basename(path) == "progress.json":
        try:
            from _internal.progress_checksum import add_checksum

            add_checksum(data)
        except ImportError:
            sys.stderr.write(
                "⚠️  progress_checksum no disponible — escribiendo progress.json sin checksum\n"
            )
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
        return _backlog_init(sub_args)
    elif subcommand == "add":
        return _backlog_add(sub_args)
    elif subcommand == "list":
        return _backlog_list(sub_args)
    elif subcommand == "prioritize":
        return _backlog_prioritize(sub_args)
    elif subcommand == "refine":
        return _backlog_refine(sub_args)
    elif subcommand == "select":
        return _backlog_select(sub_args)
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


def _backlog_init(args: list[str]) -> int:
    """Initialize an empty backlog."""
    if not args:
        eprint("❌ Uso: spring.py backlog init <backlog_dir>")
        return 2

    backlog_dir = os.path.abspath(args[0])
    path = _backlog_path(backlog_dir)

    if os.path.exists(path):
        eprint(f"⚠️  Ya existe un backlog en {backlog_dir}")
        items = _load_backlog(backlog_dir)
        print(f"   Items existentes: {len(items)}")
        return 0

    os.makedirs(backlog_dir, exist_ok=True)
    # Create empty backlog
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


def _backlog_add(args: list[str]) -> int:
    """Add an item to the backlog."""
    if not args:
        eprint('❌ Uso: spring.py backlog add <backlog_dir> --title "..." [flags]')
        eprint(
            "   Flags: --desc, --value N, --effort N, --domain X, --criteria '[...]', --scrum-role po|dev|qa"
        )
        return 2

    backlog_dir = os.path.abspath(args[0])
    flags = _parse_flags(args[1:])

    title = flags.get("title", "")
    if not title:
        eprint("❌ --title es requerido")
        return 2

    description = flags.get("desc", "")
    domain = flags.get("domain", "core")
    scrum_role = flags.get("scrum-role", "")
    try:
        business_value = int(flags.get("value", "5"))
    except ValueError:
        eprint("❌ --value debe ser un número entero (1-10)")
        return 2
    try:
        effort_estimate = int(flags.get("effort", "5"))
    except ValueError:
        eprint("❌ --effort debe ser un número entero (1-10)")
        return 2

    if business_value < 1 or business_value > 10:
        eprint("❌ --value debe estar entre 1 y 10")
        return 2
    if effort_estimate < 1 or effort_estimate > 10:
        eprint("❌ --effort debe estar entre 1 y 10")
        return 2

    # Parse criteria if provided
    criteria_raw = flags.get("criteria", "[]")
    try:
        acceptance_criteria = json.loads(criteria_raw)
    except json.JSONDecodeError:
        acceptance_criteria = []
    if not isinstance(acceptance_criteria, list):
        acceptance_criteria = []

    # Load existing items and generate ID
    items = _load_backlog(backlog_dir)
    item_id = _next_backlog_id(items)
    priority_score = _compute_priority(business_value, effort_estimate)

    item = {
        "id": item_id,
        "title": title,
        "description": description,
        "business_value": business_value,
        "effort_estimate": effort_estimate,
        "priority_score": priority_score,
        "status": "backlog",
        "acceptance_criteria": acceptance_criteria,
        "domain": domain,
        "scrum_role": scrum_role if scrum_role in ("po", "dev", "qa") else None,
        "tags": flags.get("tags", "").split(",") if flags.get("tags") else [],
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


def _backlog_list(args: list[str]) -> int:
    """List backlog items sorted by priority."""
    if not args:
        eprint(
            "❌ Uso: spring.py backlog list <backlog_dir> [--status backlog|refined|in-sprint|done]"
        )
        return 2

    backlog_dir = os.path.abspath(args[0])
    flags = _parse_flags(args[1:])
    status_filter = flags.get("status", "")

    items = _load_backlog(backlog_dir)

    if not items:
        print(f"📭 Backlog vacío en {backlog_dir}")
        print(f'   Agrega items con: spring.py backlog add {backlog_dir} --title "..."')
        return 0

    if status_filter:
        items = [it for it in items if it.get("status") == status_filter]

    if not items:
        print(f"📭 No hay items con status '{status_filter}'")
        return 0

    items = _prioritize(items)

    status_icons = {
        "backlog": "📋",
        "refined": "🔍",
        "in-sprint": "🔄",
        "done": "✅",
    }

    # Check if any items have scrum_role set
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
    if not status_filter:
        # Show summary by status
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


def _backlog_prioritize(args: list[str]) -> int:
    """Reorder a backlog item by changing its priority score."""
    if len(args) < 3:
        eprint(
            "❌ Uso: spring.py backlog prioritize <backlog_dir> <item_id> <new_rank>"
        )
        return 2

    backlog_dir = os.path.abspath(args[0])
    item_id = args[1]
    try:
        new_rank = int(args[2])
    except ValueError:
        eprint("❌ new_rank debe ser un número entero positivo")
        return 2

    if new_rank < 1:
        eprint("❌ new_rank debe ser >= 1")
        return 2

    items = _load_backlog(backlog_dir)
    # Find the item
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

    # Remove from current position
    items.pop(target_idx)

    # Adjust priority_score to place at desired rank (0-indexed)
    # Calculate the score needed to be at position rank-1
    items = _prioritize(items)

    # Actually, let's just re-insert at the right position
    # by adjusting the target's priority_score
    if new_rank > len(items) + 1:
        new_rank = len(items) + 1

    # Give the item a score that places it at the desired rank
    if new_rank == 1 and items:
        # Place above the first item
        target["priority_score"] = items[0].get("priority_score", 100) + 1.0
    elif new_rank > len(items):
        # Place at the bottom
        target["priority_score"] = (
            min(it.get("priority_score", 0) for it in items) - 1.0 if items else 1.0
        )
    else:
        # Place between two items
        items.insert(new_rank - 1, target)
        # Renormalize scores
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

    items.insert(new_rank - 1, target)
    # Renormalize scores
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


def _backlog_refine(args: list[str]) -> int:
    """Edit a field of a backlog item."""
    if len(args) < 2:
        eprint(
            "❌ Uso: spring.py backlog refine <backlog_dir> <item_id> --field <campo> <valor>"
        )
        eprint(
            "   Campos editables: title, desc, value, effort, domain, criteria, status, tags"
        )
        return 2

    backlog_dir = os.path.abspath(args[0])
    item_id = args[1]
    flags = _parse_flags(args[2:])

    field = flags.get("field", "")
    value = ""
    # Get the value after --field
    for i, a in enumerate(args):
        if a == "--field" and i + 2 < len(args):
            field = args[i + 1]
            value = args[i + 2]
            break

    if not field or not value:
        eprint("❌ --field <campo> <valor> es requerido")
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

    # Map user-friendly field names to internal keys
    field_map = {
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

    internal_key = field_map.get(field)
    if internal_key is None:
        eprint(f"❌ Campo desconocido: {field}")
        eprint("   Campos: title, desc, value, effort, domain, status, tags, criteria")
        return 2

    # Parse value type
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
        # Recompute priority score
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
    print(f"  Campo editado: {field} → {value[:60]}")
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


def _backlog_select(args: list[str]) -> int:
    """Select items for a sprint (mark them as 'in-sprint')."""
    if len(args) < 2:
        eprint("❌ Uso: spring.py backlog select <backlog_dir> <item_ids...>")
        return 2

    backlog_dir = os.path.abspath(args[0])
    item_ids = args[1:]

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


# ═══════════════════════════════════════════════════════════════
#  SPRINT PLANNING COMMANDS
# ═══════════════════════════════════════════════════════════════

# ── Schema-driven task validation (pre-write) ───────────────────
# Same list as spring.py to ensure consistency
_TASK_REQUIRED_FIELDS = [
    "task_id",
    "type",
    "objective",
    "result_state",
    "domain",
    "impacted_artifacts",
    "risks",
    "task_context",
    "validation",
]

_TASK_FIELD_DESCRIPTIONS = {
    "type": "string — feature|fix|refactor|test|docs",
    "domain": "string — modulo del proyecto (ej: auth, core, api)",
    "impacted_artifacts": 'object con {"files": ["ruta/archivo.py"]}',
    "risks": 'object con {"curvature": 0.0-1.0, "commit_trust": 0.0-1.0}',
    "task_context": "object con 6 capas de analisis",
    "validation": 'object con {"predicates": ["..."]}',
}


def _validate_task_fields(tasks: list[dict]) -> tuple[bool, list[str]]:
    """Pre-write validation: check each task has all required schema fields.

    Same logic as spring.py _validate_task_fields().
    """
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
    """Add task_context to each task (required by plan.schema.json).

    Pre-fills with extracted symbols from objective + goal.
    The LLM should refine these later via task.py research.
    """
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
        result.append(t)
    return result


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
        return _planning_propose(sub_args)
    elif subcommand == "refine":
        return _planning_refine(sub_args)
    elif subcommand == "commit":
        return _planning_commit(sub_args)
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
        print("    Convierte .planning/ en sprint real (plan.json + progress.json).")
        print("    Exit 0: sprint creado. Exit 2: error de validacion.")
        print()
        print("  El flujo completo: propose → refine (opcional) → commit")
        return 0
    else:
        eprint(f"❌ Subcomando desconocido: {subcommand}")
        eprint("   Subcomandos: propose, refine, commit")
        return 2


def _planning_propose(args: list[str]) -> int:
    """Propose a sprint plan (saves to .planning/proposal.json and blocks)."""
    if len(args) < 1:
        eprint(
            '❌ Uso: spring.py planning propose <chain_dir> --project-id X --goal "..."'
        )
        eprint(
            "   [--backlog-dir DIR] [--item-ids IDs] [--tasks JSON] [--predicates JSON]"
        )
        eprint(
            "   [--scrum-role po|dev|qa]  (default role for backlog items without scrum_role)"
        )
        return 2

    chain_dir = os.path.abspath(args[0])
    flags = _parse_flags(args[1:])

    project_id = flags.get("project-id", "")
    goal = flags.get("goal", "")
    backlog_dir = flags.get("backlog-dir", "")
    item_ids_raw = flags.get("item-ids", "")
    tasks_json = flags.get("tasks", "[]")
    predicates_json = flags.get("predicates", "{}")

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

    # Load backlog items if specified
    backlog_items = []
    if backlog_dir and item_ids_raw:
        item_ids = [i.strip() for i in item_ids_raw.split(",") if i.strip()]
        items = _load_backlog(os.path.abspath(backlog_dir))
        for iid in item_ids:
            for it in items:
                if it.get("id") == iid:
                    backlog_items.append(it)
                    break

    scrum_role_default = flags.get("scrum-role", "")

    # Validate tasks
    if not tasks:
        # Generate tasks from backlog items if available
        for item in backlog_items:
            tid = f"TASK-{len(tasks) + 1:03d}"
            # Transfer scrum_role from backlog item, fallback to --scrum-role or None
            item_role = item.get("scrum_role") or scrum_role_default or None
            tasks.append(
                {
                    "task_id": tid,
                    "type": "feature",
                    "scrum_role": item_role,
                    "domain": item.get("domain", "core"),
                    "objective": item.get("title", ""),
                    "result_state": item.get("description", item.get("title", "")),
                    "impacted_artifacts": {"files": []},
                    "risks": {"curvature": 0.20},
                    "validation": {"predicates": []},
                    "acceptance_criteria": item.get("acceptance_criteria", []),
                }
            )

        # If still empty (no backlog items), use a placeholder
        if not tasks:
            eprint("❌ No hay tareas definidas ni backlog items seleccionados.")
            eprint("   Pasa --tasks '[...]' o --backlog-dir y --item-ids")
            return 2

    # Validate each task
    for t in tasks:
        obj = t.get("objective", "")
        rs = t.get("result_state", "")
        if len(obj) < 10:
            eprint(
                f"❌ Tarea {t.get('task_id', '?')}: objective debe tener >= 10 caracteres"
            )
            return 2
        if len(rs) < 10:
            eprint(
                f"❌ Tarea {t.get('task_id', '?')}: result_state debe tener >= 10 caracteres"
            )
            return 2

    # ── PRE-WRITE VALIDATION (schema fields) ───────────────────
    # Add auto-generated task_context before validating
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
        eprint("  Referencia: schema/plan.schema.json → $defs/task → required")
        eprint()
        eprint("  Corrige las tareas y reintenta con --tasks:")
        eprint(
            f"     spring.py planning propose {args[0] if args else '<chain_dir>'} "
            "--goal \"...\" --tasks '[...]'"
        )
        eprint()
        eprint("  Template de tarea completa:")
        eprint('  {"task_id": "TASK-001", "type": "feature",')
        eprint('   "objective": "Implementar X",')
        eprint('   "result_state": "X funciona correctamente",')
        eprint('   "domain": "core",')
        eprint('   "impacted_artifacts": {"files": ["ruta/archivo.py"]},')
        eprint('   "risks": {"curvature": 0.20, "commit_trust": 0.70},')
        eprint('   "task_context": {"observation": "...", ...},')
        eprint('   "validation": {"predicates": ["Una función X existe"]}}')
        eprint("=" * 70)
        return 2

    # Normalize predicates
    if isinstance(predicates, list):
        predicates = {
            "must_exist": [p for p in predicates if not p.startswith("NO ")],
            "must_not_exist": [p[3:] for p in predicates if p.startswith("NO ")],
            "invariants": [],
        }
    elif isinstance(predicates, dict):
        predicates.setdefault("must_exist", [])
        predicates.setdefault("must_not_exist", [])
        predicates.setdefault("invariants", [])

    # Build proposal
    now = _now_iso()
    chain_id = os.path.basename(os.path.normpath(chain_dir))

    proposal = {
        "meta": {
            "chain_id": chain_id,
            "project_id": project_id,
            "goal": goal,
            "generated_at": now,
            "status": "proposed",
            "backlog_source": os.path.basename(os.path.normpath(backlog_dir))
            if backlog_dir
            else None,
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

    # Save to .planning/
    planning_dir = os.path.join(chain_dir, ".planning")
    os.makedirs(planning_dir, exist_ok=True)
    proposal_path = os.path.join(planning_dir, "proposal.json")
    write_json(proposal_path, proposal)

    # Show the proposal to the user
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
    print(f'     spring.py planning refine {chain_dir} --edit "campo=valor"')
    print()
    print("  ▶️  Para CONFIRMAR y crear el sprint:")
    print(f"     spring.py planning commit {chain_dir}")
    print()
    print(
        "⚠️  El plan está en .planning/proposal.json. Usa commit para hacerlo efectivo."
    )
    print("=" * 70)

    return 3


def _planning_refine(args: list[str]) -> int:
    """Edit a field in the proposed sprint plan."""
    if len(args) < 1:
        eprint('❌ Uso: spring.py planning refine <chain_dir> --edit "campo=valor"')
        eprint("   Campos editables: goal, tasks, predicates, project-id")
        return 2

    chain_dir = os.path.abspath(args[0])
    proposal_path = os.path.join(chain_dir, ".planning", "proposal.json")

    proposal = load_json(proposal_path)
    if proposal is None:
        eprint(f"❌ No hay propuesta en {chain_dir}. Ejecuta propose primero.")
        return 2

    if proposal.get("meta", {}).get("status") != "proposed":
        eprint(
            f"❌ La propuesta ya fue procesada (status: {proposal.get('meta', {}).get('status')})"
        )
        return 2

    # Parse --edit
    edit_value = ""
    for i, a in enumerate(args):
        if a == "--edit" and i + 1 < len(args):
            edit_value = args[i + 1]
            break
        if a.startswith("--edit="):
            edit_value = a[7:]
            break

    if not edit_value:
        eprint('❌ --edit "campo=valor" es requerido')
        return 2

    # Parse campo=valor or campo="valor con espacios"
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
        # tasks:add "{\"objective\":\"...\",...}"
        # tasks:remove TASK-001
        # tasks:set '[...]'
        sub_parts = field.split(":", 1)
        if len(sub_parts) == 2:
            sub_cmd = sub_parts[1]
            if sub_cmd == "add":
                try:
                    new_task = json.loads(value)
                except json.JSONDecodeError:
                    eprint("❌ tasks:add requiere JSON de tarea")
                    return 2

                # ── Validate the new task against schema ──
                goal = proposal.get("meta", {}).get("goal", "")
                enriched = _add_task_context_to_tasks([new_task], goal)
                valid, errors = _validate_task_fields(enriched)
                if not valid:
                    eprint()
                    eprint("=" * 70)
                    eprint("❌ VALIDACIÓN: La tarea nueva no cumple el schema")
                    eprint("=" * 70)
                    for e in errors:
                        eprint(e)
                    eprint()
                    eprint(
                        "  Referencia: schema/plan.schema.json → $defs/task → required"
                    )
                    eprint()
                    eprint("  Template de tarea completa:")
                    eprint('  {"task_id": "TASK-001", "type": "feature",')
                    eprint('   "objective": "Implementar X",')
                    eprint('   "result_state": "X funciona correctamente",')
                    eprint('   "domain": "core",')
                    eprint('   "impacted_artifacts": {"files": ["ruta/archivo.py"]},')
                    eprint('   "risks": {"curvature": 0.20, "commit_trust": 0.70},')
                    eprint('   "task_context": {"observation": "...", ...},')
                    eprint('   "validation": {"predicates": ["Una función X existe"]}}')
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

            # ── Validate all new tasks against schema ──
            goal = proposal.get("meta", {}).get("goal", "")
            enriched = _add_task_context_to_tasks(new_tasks, goal)
            valid, errors = _validate_task_fields(enriched)
            if not valid:
                eprint()
                eprint("❌ VALIDACIÓN: Las nuevas tareas no cumplen el schema")
                for e in errors:
                    eprint(e)
                eprint("  Referencia: schema/plan.schema.json → $defs/task → required")
                eprint()
                eprint("  Template de tarea completa:")
                eprint('  {"task_id": "TASK-001", "type": "feature",')
                eprint('   "objective": "Implementar X",')
                eprint('   "result_state": "X funciona correctamente",')
                eprint('   "domain": "core",')
                eprint('   "impacted_artifacts": {"files": ["ruta/archivo.py"]},')
                eprint('   "risks": {"curvature": 0.20, "commit_trust": 0.70},')
                eprint('   "task_context": {"observation": "...", ...},')
                eprint('   "validation": {"predicates": ["Una función X existe"]}}')
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

    # Save updated proposal
    write_json(proposal_path, proposal)

    # Show updated summary
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


def _planning_commit(args: list[str]) -> int:
    """Commit the planning proposal: create plan.json + progress.json."""
    if not args:
        eprint("❌ Uso: spring.py planning commit <chain_dir>")
        return 2

    chain_dir = os.path.abspath(args[0])
    proposal_path = os.path.join(chain_dir, ".planning", "proposal.json")

    proposal = load_json(proposal_path)
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

    # ── Add auto-generated fields before validation ────────────
    # task_context is generated by the script (not provided by LLM in planning flow)
    tasks = _add_task_context_to_tasks(tasks, goal)

    # ── PRE-WRITE VALIDATION ──
    valid, field_errors = _validate_task_fields(tasks)
    if not valid:
        eprint()
        eprint("=" * 70)
        eprint("❌ VALIDACIÓN PRE-WRITE: Faltan campos requeridos en las tareas")
        eprint("=" * 70)
        for err in field_errors:
            eprint(err)
        eprint()
        eprint("  Referencia: schema/plan.schema.json → $defs/task → required")
        eprint()
        eprint("  Corrige las tareas en la propuesta y reintenta:")
        eprint(f'     spring.py planning refine {chain_dir} --edit "tasks=..."')
        eprint()
        eprint("  Template de tarea completa:")
        eprint('  {"task_id": "TASK-001", "type": "feature",')
        eprint('   "objective": "Implementar X",')
        eprint('   "result_state": "X funciona correctamente",')
        eprint('   "domain": "core",')
        eprint('   "impacted_artifacts": {"files": ["ruta/archivo.py"]},')
        eprint('   "risks": {"curvature": 0.20, "commit_trust": 0.70},')
        eprint('   "task_context": {"observation": "...", ...},')
        eprint('   "validation": {"predicates": ["Una función X existe"]}}')
        eprint("=" * 70)
        return 2

    # ── Build plan.json ───────────────────────────────────────
    plan = {
        "plan": {
            "meta": {
                "chain_id": chain_id,
                "generated_at": now,
                "version": "1.0",
                "source": f"{project_id}:planning",
                "type": "plan",
            },
            "project": {
                "project_id": project_id,
                "project_type": "backend",
                "domains": list(set(t.get("domain", "core") for t in tasks)),
                "tags": ["python"],
            },
            "goal": {
                "description": goal,
                "path_summary": "",
                "predicates": predicates,
                "signatures": [],
            },
            "tasks": tasks,
            "parameters": {},
        }
    }

    # ── Build progress.json ───────────────────────────────────
    next_action = None
    task_states = {}
    for t in _add_task_context_to_tasks(tasks, goal):
        tid = t.get("task_id", "")
        task_states[tid] = "pending"
        if next_action is None:
            next_action = {
                "task_id": tid,
                "type": t.get("type", "feature"),
                "work_type": t.get("work_type", "refactor"),
                "target": "",
                "delta": 0.0,
                "confidence": t.get("risks", {}).get("commit_trust", 0.70),
                "risk": t.get("risks", {}).get("curvature", 0.20),
                "score": 0.0,
                "candidate": t.get("objective", "")[:100],
                "rationale": f"Planning: {t.get('objective', '')[:60]}...",
            }

    progress = {
        "progress": {
            "meta": {
                "chain_id": chain_id,
                "generated_at": now,
                "version": "1.0",
                "source": f"{project_id}:planning",
                "type": "progress",
                "status": "active",
            },
            "plan_ref": chain_id,
            "trail": [],
            "current": {
                "task_states": task_states,
                "next_action": next_action,
                "alternatives": [],
                "risks": [],
                "delta_metrics": {},
            },
        }
    }

    # ── Write files ───────────────────────────────────────────
    os.makedirs(chain_dir, exist_ok=True)
    plan_path = os.path.join(chain_dir, "plan.json")
    progress_path = os.path.join(chain_dir, "progress.json")

    write_json(plan_path, plan)
    write_json(progress_path, progress)

    # ── ENFORCEMENT: Validate schemas post-write ────────────────
    # Same pattern as cmd_create in spring.py — dirty data must not persist.
    _script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _base_dir = os.path.dirname(_script_dir)
    import subprocess

    def _validate_schema_local(path: str, schema_name: str) -> bool:
        schema_path = os.path.join(_base_dir, "schema", schema_name)
        validate_script = os.path.join(_script_dir, "validate-schema.py")
        if not os.path.exists(validate_script) or not os.path.exists(schema_path):
            eprint("  ❌ VALIDATION ENFORCEMENT: validador o schema no encontrado")
            return False
        r = subprocess.run(
            [sys.executable, validate_script, path, schema_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode != 0 and r.stdout:
            for line in r.stdout.splitlines():
                if any(
                    tag in line
                    for tag in ("❌", "Mensaje:", "Error", "#1", "#2", "RECOVERY")
                ):
                    eprint(f"  {line.strip()}")
        return r.returncode == 0

    plan_valid = _validate_schema_local(plan_path, "plan.schema.json")
    prog_valid = _validate_schema_local(progress_path, "progress.schema.json")

    if not plan_valid or not prog_valid:
        eprint()
        eprint("=" * 70)
        eprint("❌ ENFORCEMENT BLOCKED: Schema validation failed")
        eprint("=" * 70)
        try:
            if os.path.exists(plan_path):
                os.remove(plan_path)
            if os.path.exists(progress_path):
                os.remove(progress_path)
        except OSError:
            pass
        return 2

    # ── Update proposal status ────────────────────────────────
    proposal["meta"]["status"] = "committed"
    proposal["meta"]["committed_at"] = now
    write_json(proposal_path, proposal)

    # ── Update backlog items status ───────────────────────────
    # Note: items already marked by backlog select. This is redundancy
    # for the case where items were passed via --item-ids without select.
    backlog_dir = proposal["meta"].get("backlog_source")
    backlog_items = proposal.get("backlog_items", [])
    if backlog_dir and backlog_items:
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
    print("  ▶️  Primera tarea:")
    if next_action:
        print(
            f"     python3 scripts/task.py advance {chain_dir} {next_action['task_id']}"
        )
    print()
    print("  ▶️  Para ver estado:")
    print(f"     python3 scripts/spring.py status {chain_dir}")
    print()
    print("⚠️  Importante: task.py advance ahora requiere --confirm primero")
    print("    y luego --human-confirm (solo el usuario) para completar tareas.")
    print("=" * 70)

    return 0
