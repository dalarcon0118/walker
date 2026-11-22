"""handlers/capture.py — capture handler.

Records evidence/artifacts for a task.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult
from fast_mcp.features.tasks.handlers._inspect_codebase_helper import run_inspect_codebase


def _normalize_ledger(value: Any) -> dict[str, Any] | None:
    """Normaliza un `task_ledger` y devuelve None si no es capturable.

    El artefacto es la pieza que faltaba para la metacognición: sin él, el
    agente tiene plan y código, pero ningún rastro de su razonamiento (hipótesis,
    evidencia, descartes). El gate valida la FORMA, no la virtud (AE-INV-06):
    se exige al menos un problema o una hipótesis, no un ensayo.
    """
    if not isinstance(value, dict):
        return None
    ledger = {
        "problem": str(value.get("problem") or "").strip(),
        "options": list(value.get("options") or []),
        "chosen": str(value.get("chosen") or "").strip(),
        "rejected_because": dict(value.get("rejected_because") or {}),
        "invariants_touched": list(value.get("invariants_touched") or []),
        "hypotheses": list(value.get("hypotheses") or []),
        "evidence": list(value.get("evidence") or []),
        "uncertainties": list(value.get("uncertainties") or []),
        "dead_ends": list(value.get("dead_ends") or []),
        "adjust": str(value.get("adjust") or "").strip(),
        "files": list(value.get("files") or []),
        "denials": int(value.get("denials") or 0),
        "scope_expanded": int(value.get("scope_expanded") or 0),
        "writes": int(value.get("writes") or 0),
        "verified": value.get("verified"),
    }
    if not ledger["problem"] and not ledger["hypotheses"] and not ledger["files"] \
       and not ledger["evidence"] and not ledger["denials"] \
       and not ledger["scope_expanded"] and not ledger["writes"] \
       and not ledger["uncertainties"] and not ledger["dead_ends"]:
        return None
    return ledger


def build_ledger_skeleton(chain_dir: str, session_id: str, task_id: str = "") -> dict[str, Any]:
    """Prefill del `task_ledger` desde el trace (F1b).

    Devuelve un skeleton con los campos que el trace ya sabe (archivos, tests,
    denials, scope, writes, verified) prellenados y los campos de razonamiento
    (problem, hypotheses, options, chosen, etc.) vacíos para que el agente
    corrija/completa. Nada se inventa: si el trace no tiene un dato, queda
    vacío (no se rellena con el goal de otra sesión ni con un default).
    """
    skeleton: dict[str, Any] = {
        "problem": "",
        "options": [],
        "chosen": "",
        "rejected_because": {},
        "invariants_touched": [],
        "hypotheses": [],
        "evidence": [],
        "uncertainties": [],
        "dead_ends": [],
        "adjust": "",
        "files": [],
        "denials": 0,
        "scope_expanded": 0,
        "writes": 0,
        "verified": None,
    }
    if not chain_dir or not session_id:
        return skeleton
    try:
        import importlib

        _mod = importlib.import_module(
            "fast_mcp.features.event_tracing.application.services.trace_factory"
        )
        build_trace_service = _mod.build_trace_service
        service = build_trace_service(chain_dir)
        if service is None:
            return skeleton
        events = [
            e for e in service.load_traces(chain_dir)
            if str((getattr(e, "metadata", None) or {}).get("session_id", "")) == session_id
        ]
    except Exception:  # noqa: BLE001
        return skeleton

    files: list[str] = []
    denials = 0
    scope_expanded = 0
    writes = 0
    verified = None
    test_results: list[dict[str, Any]] = []
    for event in events:
        et = str(getattr(event, "event_type", "") or "")
        data = getattr(event, "data", None) or {}
        if not isinstance(data, dict):
            data = {}
        if et in ("session_state", "session_metrics"):
            writes = int(data.get("writes") or writes)
            if "verified" in data:
                verified = bool(data["verified"])
            for f in (data.get("touched") or []):
                if f and f not in files:
                    files.append(str(f))
        elif et == "flight_control_denied":
            denials += 1
        elif et == "flight_control_scope_expanded":
            scope_expanded += 1
        elif et == "run_tests" or et == "task_completed":
            passed = data.get("passed")
            total = data.get("total")
            if passed is not None or total is not None:
                test_results.append({
                    "criterion": "suite",
                    "check": "run_tests",
                    "ref": f"passed={passed} total={total}",
                    "result": "pass" if (passed is not None and passed == total and (total or 0) > 0) else "fail",
                })

    # Also gather files from task specs (via delta) if available.
    try:
        from fast_mcp.composition import build_sprint_service

        svc = build_sprint_service(chain_dir)
        if svc is not None:
            session = svc.get_session(session_id) or {}
            spring_id = str(session.get("spring_id") or "")
            if spring_id:
                for spec in (svc.get_specs(spring_id) or []):
                    ctx_task = (spec.get("task_context") or {}).get("task_id") or ""
                    if task_id and ctx_task and ctx_task != task_id:
                        continue
                    for f in (spec.get("files") or []):
                        if f and f not in files:
                            files.append(str(f))
                    for c in (spec.get("acceptance_criteria") or []):
                        if c and not any(e.get("criterion") == c for e in test_results):
                            test_results.append({
                                "criterion": str(c),
                                "check": "predicate",
                                "ref": "spec",
                                "result": "pending",
                            })
    except Exception:  # noqa: BLE001
        pass

    skeleton["files"] = files
    skeleton["denials"] = denials
    skeleton["scope_expanded"] = scope_expanded
    skeleton["writes"] = writes
    skeleton["verified"] = verified
    if test_results:
        skeleton["evidence"] = test_results
    if denials:
        skeleton["uncertainties"].append(f"{denials} denial(es) de gate en la sesión")
    if scope_expanded:
        skeleton["uncertainties"].append(f"{scope_expanded} archivo(s) ampliado(s) fuera del plan original")
    return skeleton


def _merge_ledger(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge: el skeleton (trace) es la base; el agente sobrescribe/completa."""
    result = dict(base)
    for key, value in (override or {}).items():
        if key in ("files", "evidence", "hypotheses", "options", "uncertainties", "dead_ends",
                   "invariants_touched", "rejected_because"):
            if value:
                result[key] = value
        else:
            if value not in (None, "", [], {}):
                result[key] = value
    return result


def _save_ledger(chain_dir: str, ledger: dict[str, Any], task_id: str, session_id: str) -> dict[str, Any]:
    """Persiste el `task_ledger` en el CKG (idempotente por (session, task))."""
    if not chain_dir:
        return {"saved": False, "error": "chain_dir no disponible"}
    try:
        from fast_mcp.composition import ckg_for, ckg_save_nodes

        # Idempotencia por (session, task): una sola reflexión por tarea.
        ckg = ckg_for(chain_dir)
        if ckg is not None:
            try:
                existing = ckg.query({"type": "task-ledger", "metadata.session_id": session_id,
                                      "metadata.task_id": task_id}) if (task_id and session_id) else []
                if existing:
                    return {"saved": False, "skipped": True,
                            "reason": "task_ledger ya registrado para la tarea",
                            "node_id": existing[0].get("_id", "")}
            except Exception:  # noqa: BLE001
                pass

        node_id = f"task_ledger-{session_id or 'anon'}-{task_id or 'anon'}"
        node = {
            "id": node_id,
            "label": (ledger.get("problem") or task_id or "task_ledger")[:200],
            "description": json.dumps(ledger, ensure_ascii=False),
            "type": "task_ledger",
            "tags": ["task_ledger", "session", "reflection"],
            "metadata": {
                "session_id": session_id or "",
                "task_id": task_id or "",
                "spec_id": task_id or "",
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "applies_to": [],
            },
        }
        out = ckg_save_nodes(chain_dir, [node], task_id=task_id, session_id=session_id)
        if not out.get("ok"):
            return {"saved": False, "error": out.get("error", "fallo al guardar")}
        return {"saved": True, "node_id": node_id}
    except Exception as exc:  # noqa: BLE001
        return {"saved": False, "error": f"{type(exc).__name__}: {exc}"}


def capture(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Capture evidence/artifacts for a task.

    W5 (ADR-076): si ctx.cpg_adapter está disponible y `data` incluye
    `inspect_codebase_action`, ejecuta la action y añade el resultado a
    los artifacts.

    ADR-084 F1a: si `data` incluye `ledger`, persiste un `task_ledger` en el
    CKG ligado a la sesión y a la tarea (idempotente por (session, task)).
    ADR-084 F1b: si `data` incluye `ledger="auto"` o `prefill=True`, construye
    el skeleton desde el trace y lo mergea con los campos que el agente provea.
    """
    evidence = data.get("evidence", "")
    artifacts = data.get("artifacts", [])
    task_id = data.get("task_id") or ctx.task_id
    session_id = str(data.get("session_id") or getattr(ctx, "session_id", "") or "").strip()

    # ADR-084: capture no se declara verde si quedan gaps sin remediar. El
    # plugin puede forzar con `force=true`, pero por defecto se bloquea para
    # que la evidencia no se escriba sobre una sesión roja.
    if session_id and not data.get("force"):
        try:
            import importlib

            _mod = importlib.import_module(
                "fast_mcp.features.event_tracing.application.services.trace_factory"
            )
            build_trace_service = _mod.build_trace_service
            _kmod = importlib.import_module("fast_mcp.memories.handlers.knowledge")
            _project_flight_state = _kmod._project_flight_state

            service = build_trace_service(ctx.chain_dir)
            if service is not None:
                events = [
                    e for e in service.load_traces(ctx.chain_dir)
                    if str((getattr(e, "metadata", None) or {}).get("session_id", "")) == session_id
                ]
                flight = _project_flight_state(events, ctx.chain_dir, session_id)
                gaps = flight.get("gaps") or []
                if gaps:
                    detail = "; ".join(
                        str(g.get("detail", g) if isinstance(g, dict) else g)
                        for g in gaps[:3]
                    )
                    remedy = "; ".join(
                        str(g.get("remedy", "") if isinstance(g, dict) else "")
                        for g in gaps[:3] if isinstance(g, dict) and g.get("remedy")
                    )
                    msg = f"capture: hay gaps sin remediar: {detail}."
                    if remedy:
                        msg += f" Remedio: {remedy}."
                    msg += " Corregí o pasá force=true."
                    return TaskResult.failure(msg)
        except Exception:  # noqa: BLE001 — sin trace no se bloquea
            pass

    inspect_result = run_inspect_codebase(getattr(ctx, "cpg_adapter", None), data)
    if inspect_result is not None:
        artifacts.append({"type": "inspect_codebase", "result": inspect_result})

    # F1b: prefill desde el trace si se pide.
    raw_ledger = data.get("ledger")
    if raw_ledger == "auto" or data.get("prefill"):
        skeleton = build_ledger_skeleton(ctx.chain_dir, ctx.session_id, task_id)
        if isinstance(raw_ledger, dict):
            raw_ledger = _merge_ledger(skeleton, raw_ledger)
        else:
            raw_ledger = skeleton

    ledger = _normalize_ledger(raw_ledger)
    ledger_result: dict[str, Any] = {"saved": False}
    if ledger is not None:
        ledger_result = _save_ledger(ctx.chain_dir, ledger, task_id, ctx.session_id)

    return TaskResult.success(data={
        "task_id": task_id,
        "captured": True,
        "evidence": evidence,
        "artifacts": artifacts,
        "chain_dir": ctx.chain_dir,
        "ledger": ledger_result,
        "skeleton": raw_ledger if raw_ledger and isinstance(raw_ledger, dict) else None,
    })


def register(registry) -> None:
    """Register capture."""
    registry.register("capture", capture, metadata={
        "description": "Capture task evidence/artifacts",
        "tags": ["evidence"],
        "scope": "internal",
    })