"""handlers/spec_predicates.py — adjuntar predicados ejecutables a una spec.

Hueco medido 2026-11-22: una spec derivaba criterios en prosa, la heurística
no podía convertirlos en predicados y quedaban en `unmapped` para siempre. El
agente sí sabía cómo verificarlos (`npx spago test`, un `grep`, un archivo),
pero no había forma de declararlo: `verify_predicates` leía los predicados de
la spec y nadie podía escribirlos.

Este tool cierra ese tramo: asocia `{criterion, predicate}` al bundle de la
spec y saca el criterio de `unmapped`. Es la fuente que después consume
`verify_predicates` cuando el caller no pasa predicados.

Contrato:
  - `predicate` puede ser el dict del verify_engine o un string (comando).
  - `mode=add` (default) suma; `mode=set` reemplaza el bundle.
  - Idempotente por (type, command|target|path).
  - Un predicado inválido no se inventa: el criterio queda en `unmapped` con
    el motivo.
"""
from __future__ import annotations

from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult
from fast_mcp.features.tasks.handlers import _di, _trace


def _norm(text: object) -> str:
    """Normaliza para comparar criterios: minúsculas, sin acentos, sin espacios extra.

    El mismo criterio llega como "área" desde la spec y "area" desde el caller:
    sin plegar acentos, el criterio nunca se considera cubierto y el hueco
    persiste aunque el predicado esté adjunto.
    """
    import unicodedata

    folded = unicodedata.normalize("NFKD", str(text or ""))
    plain = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return " ".join(plain.lower().split())


def _predicate_key(pred: dict) -> tuple:
    return (
        str(pred.get("type") or ""),
        str(pred.get("command") or pred.get("target") or pred.get("path") or ""),
    )


def _split_entry(entry: Any, normalize_predicate) -> tuple[str, dict | None]:
    """Devuelve (criterio, predicado normalizado) de una entrada del caller."""
    if isinstance(entry, str):
        text = entry.strip()
        return text, normalize_predicate(text, text)
    if not isinstance(entry, dict):
        return "", None
    if "predicate" in entry:
        text = str(entry.get("criterion") or entry.get("text") or "").strip()
        return text, normalize_predicate(entry.get("predicate"), text)
    # Predicado "suelto" con su criterio declarado como description/criterion.
    text = str(entry.get("criterion") or entry.get("description") or "").strip()
    return text, normalize_predicate(entry, text)


def spec_predicates(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    from fast_mcp.features.tasks.handlers.spec_derive import normalize_predicate

    session_id = str(data.get("session_id") or ctx.session_id or "").strip()
    task_id = str(data.get("task_id") or ctx.task_id or "").strip()
    spec_id = str(data.get("spec_id") or "").strip()
    spring_id = str(data.get("spring_id") or data.get("sprint_id") or "").strip()
    mode = str(data.get("mode") or "add").strip().lower()
    if mode not in ("add", "set"):
        return TaskResult.failure("spec_predicates: mode debe ser add|set")

    entries = data.get("criteria")
    if entries is None:
        entries = data.get("predicates")
    if isinstance(entries, (str, dict)):
        entries = [entries]
    if not isinstance(entries, list) or not entries:
        return TaskResult.failure(
            "spec_predicates: pasá `criteria` (o `predicates`): "
            '{"criteria":[{"criterion":"...","predicate":{"type":"command_exit",'
            '"command":"..."}}]}'
        )

    service = getattr(ctx, 'sprint_service', None) or _di.sprint_service(ctx.chain_dir)
    if service is None:
        return TaskResult.failure("spec_predicates: sprint service no disponible")

    if not spring_id and session_id and hasattr(service, "get_session"):
        try:
            record = service.get_session(session_id) or {}
            spring_id = str(record.get("spring_id") or "")
        except Exception as exc:  # noqa: BLE001
            return TaskResult.failure(f"spec_predicates: {type(exc).__name__}: {exc}")
    if not spring_id:
        try:
            active = service.get_active_spring()
            spring_id = str((active or {}).get("spring_id") or "") if active else ""
        except Exception:  # noqa: BLE001 — sin activo se falla abajo
            spring_id = ""
    if not spring_id:
        return TaskResult.failure(
            "spec_predicates: no se pudo resolver el sprint (pasá spring_id o session_id)"
        )

    spec: dict | None = None
    if spec_id:
        spec = service.get_spec(spring_id, spec_id)
        if spec is None:
            return TaskResult.failure(
                f"spec_predicates: spec desconocida {spec_id!r} en {spring_id}"
            )
    else:
        spec = _di.current_spec(
            ctx.chain_dir, task_id=task_id, spring_id=spring_id
        )
        if spec is None:
            return TaskResult.failure(
                "spec_predicates: no hay spec para la tarea "
                f"{task_id or '(sin task_id)'} en {spring_id}; pasá spec_id"
            )
    real_spec_id = str(spec.get("spec_id") or "")

    raw_preds = spec.get("predicates")
    if isinstance(raw_preds, list):
        # Predicados legacy (lista de strings) -> convertir a bundle.
        bundle = {"items": [], "unmapped": [{"criterion": str(p)} for p in raw_preds if p]}
    elif isinstance(raw_preds, dict):
        bundle = raw_preds
    else:
        bundle = {}
    items = [p for p in (bundle.get("items") or []) if isinstance(p, dict)]
    unmapped = [u for u in (bundle.get("unmapped") or []) if isinstance(u, dict)]
    if mode == "set":
        items = []

    mapped: list[str] = []
    invalid: list[dict] = []
    valid = 0
    for entry in entries:
        criterion, pred = _split_entry(entry, normalize_predicate)
        if pred is None:
            if criterion:
                invalid.append({
                    "criterion": criterion,
                    "reason": "predicado inválido (type soportado + command/path)",
                })
            continue
        valid += 1
        if criterion:
            mapped.append(criterion)
        key = _predicate_key(pred)
        if not any(_predicate_key(p) == key for p in items):
            items.append(pred)

    covered = {
        _norm(p.get("criterion") or p.get("description"))
        for p in items
        if p.get("criterion") or p.get("description")
    }
    remaining = [
        u for u in unmapped
        if _norm(u.get("criterion")) not in covered
    ]
    for item in invalid:
        if not any(_norm(u.get("criterion")) == _norm(item["criterion"]) for u in remaining):
            remaining.append(item)

    if valid == 0:
        # No se toca la spec: `mode=set` con entradas inválidas la vaciaría.
        razones = "; ".join(
            f"{i['criterion']}: {i['reason']}" for i in invalid[:3]
        ) or "sin criterio ni predicado reconocible"
        return TaskResult.failure(f"spec_predicates: predicado inválido — {razones}")

    acceptance = [str(c) for c in (spec.get("acceptance_criteria") or [])]
    not_in_spec = [
        c for c in mapped if not any(_norm(c) == _norm(a) for a in acceptance)
    ]

    # Un criterio con predicado ES aceptación: si no estaba en la spec, se
    # escribe en el MISMO save. Antes se reportaba `criteria_not_in_spec` y se
    # descartaba, así que la spec seguía sin criterios y `spec_start` la veía
    # incompleta aunque el predicado ya existiera.
    updates: dict = {"predicates": {"items": items, "unmapped": remaining}}
    if not_in_spec:
        updates["acceptance_criteria"] = acceptance + not_in_spec

    try:
        updated = service.update_spec(spring_id, real_spec_id, updates)
    except Exception as exc:  # noqa: BLE001
        return TaskResult.failure(f"spec_predicates: {type(exc).__name__}: {exc}")
    if updated is None:
        return TaskResult.failure(
            f"spec_predicates: no se pudo actualizar {real_spec_id} en {spring_id}"
        )

    # F2 (ADR-WALKER-086): un criterio sin predicado es el disfraz más barato
    # (se declara "hecho" sin nada ejecutable). Se emite al Trace para que el
    # harness y el reporte lo puedan contar, en vez de quedar en un dict.
    if remaining:
        _trace.push(
            ctx.chain_dir,
            {
                "event_type": "criteria_unmapped",
                "source": "tasks",
                "tool": "spec_predicates",
                "task_id": real_spec_id,
                "data": {
                    "spec_id": real_spec_id,
                    "spring_id": spring_id,
                    "unmapped": [str((u or {}).get("criterion") or "") for u in remaining][:10],
                    "count": len(remaining),
                },
            },
        )

    _tc = spec.get("task_context") or {}
    _contract = _tc.get("work_contract") if isinstance(_tc, dict) else {}
    _goal = str(
        ((_contract or {}).get("goal") if isinstance(_contract, dict) else "")
        or spec.get("objective")
        or ""
    )
    return TaskResult.success(data={
        "ok": True,
        "spring_id": spring_id,
        "spec_id": real_spec_id,
        "mode": mode,
        "goal": _goal,
        "mapped": mapped,
        "predicates": items,
        "unmapped": remaining,
        "criteria_not_in_spec": not_in_spec,
        "criteria_added": not_in_spec,
        "message": (
            f"{len(items)} predicado(s) en {real_spec_id}; "
            f"{len(remaining)} criterio(s) sin predicado"
        ),
    })


def register(registry) -> None:
    registry.register(
        "spec_predicates",
        spec_predicates,
        metadata={
            "description": "Attach executable predicates to spec criteria",
            "tags": ["session", "spec", "verification"],
            "scope": "internal",
        },
    )
