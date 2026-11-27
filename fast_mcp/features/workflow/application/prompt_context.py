"""application/prompt_context.py — datos que los templates esperan y nadie llenaba.

Los `.prompt.json` usan `{what_happened}`, `{step_quality_*}`, `{task_table}`,
`{risk_table}`, `{predicates}`, `{decisions_list}` y las variables de
reflexión (`{source_node}`, `{attempt}`, `{error_context}`, ...). El
`PromptManager` solo renderiza lo que recibe: si nadie arma esas claves, el
placeholder queda vacío y el agente ve un briefing con secciones en blanco
(medido 2026-11-18 en todos los gates `user_input`).

Este módulo arma el `extra_data` desde datos REALES:
  * el último paso ejecutado (node_executions) → `what_happened` y calidad
  * la spec y el sprint (DeltaGraph) → `task_table`, `risk_table`, ...
  * el TraceGraph → reflexión de REFINE
  * el CKG → decisiones relacionadas

Todo es fail-open: si una fuente no responde, la clave se omite y el template
mantiene su placeholder vacío en vez de inventar contenido.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_WHAT_HAPPENED_MAX = 600
_LIST_LIMIT = 10
_DECISION_FILTERS = {"type": "decision"}


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        try:
            return dict(value.to_dict())
        except Exception:  # noqa: BLE001
            return {}
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {}


def _executions(state: Any) -> list:
    execs = getattr(state, "node_executions", None)
    if execs is None and isinstance(state, dict):
        execs = state.get("node_executions")
    return list(execs or [])


def _execution_data(execution: Any) -> dict:
    """`task_result.data` si existe; si no, los campos enviados."""
    entry = _as_dict(execution)
    result = entry.get("task_result")
    if hasattr(result, "to_dict"):
        result = result.to_dict()
    if isinstance(result, dict):
        data = result.get("data", result)
        if isinstance(data, dict) and data:
            return data
    fields = entry.get("submitted_fields")
    return fields if isinstance(fields, dict) else {}


def _first(data: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def last_step_summary(state: Any) -> str:
    """`what_happened`: qué hizo el paso anterior, desde su resultado real."""
    execs = _executions(state)
    if not execs:
        return ""
    entry = _as_dict(execs[-1])
    data = _execution_data(execs[-1])
    action = str(entry.get("action") or "").strip()
    where = ".".join(
        str(part) for part in (entry.get("node"), entry.get("sub_state")) if part
    )
    detail = _first(
        data,
        ("summary", "statement", "message", "fact", "decision", "status", "result"),
    )
    if not detail and isinstance(data.get("result"), dict):
        detail = _first(data["result"], ("summary", "message", "status"))
    error = str(entry.get("error") or "").strip()
    ok = entry.get("task_ok")
    head = action or where or "paso"
    if ok is False or error:
        head = f"{head} (falló)"
    parts = [f"{head} — {where}" if where and action else head]
    if detail:
        parts.append(detail)
    if error:
        parts.append(f"error: {error}")
    files = data.get("files")
    if isinstance(files, list) and files:
        parts.append("archivos: " + ", ".join(str(f) for f in files[:_LIST_LIMIT]))
    # Veredictos sin texto (booleans/contadores): `compliant`, `passed`,
    # `all_satisfied`. Sin esto, el gate mostraba solo el nombre de la acción.
    verdicts = [
        f"{key}={data[key]}"
        for key in ("compliant", "passed", "all_satisfied", "failed_count", "total")
        if key in data
    ]
    deviations = data.get("deviations")
    if isinstance(deviations, list) and deviations:
        verdicts.append(
            "desviaciones: " + "; ".join(str(d)[:80] for d in deviations[:3])
        )
    if verdicts:
        parts.append(" · ".join(verdicts))
    text = " · ".join(parts)
    return text[:_WHAT_HAPPENED_MAX]


def quality_context(state: Any) -> dict[str, Any]:
    """Calidad del paso anterior (`step_quality_*` / `step_failed_gates`).

    Sale de la última ejecución que reportó `passed` o `findings` (por ejemplo
    CODE_REVIEW_SCAN). Sin ese dato, no se inventa un "OK".
    """
    for execution in reversed(_executions(state)):
        data = _execution_data(execution)
        if "passed" not in data and "findings" not in data:
            continue
        passed = data.get("passed")
        findings = data.get("findings") or []
        listed = []
        for finding in findings if isinstance(findings, list) else []:
            if isinstance(finding, dict):
                message = (
                    finding.get("message")
                    or finding.get("detail")
                    or finding.get("rule")
                )
                # Hallazgos del CPG: traen `function`/`file`/`line`, no un
                # `message`. Sin esto el prompt mostraba el dict crudo.
                if not message and finding.get("function"):
                    where = ":".join(
                        str(x)
                        for x in (finding.get("file"), finding.get("line"))
                        if x not in (None, "")
                    )
                    message = (
                        f"{finding['function']} ({where})" if where else finding["function"]
                    )
                listed.append(str(message or finding))
            else:
                listed.append(str(finding))
        return {
            "step_quality_passed": "sí" if passed else "no",
            "step_quality_summary": _first(data, ("summary", "message", "status"))
            or ("sin hallazgos" if passed else "revisión con hallazgos"),
            "step_failed_gates": "; ".join(listed[:_LIST_LIMIT]) or "(ninguno)",
            "step_recommend": "continuar"
            if passed
            else "corregir los hallazgos antes de avanzar",
        }
    return {}


def predicate_verdict(state: Any) -> str:
    """Veredicto de predicados del último PREDICATE_CHECK: "true"|"false"|"sin verificar".

    El gate del PO necesita este dato y no estaba en ningún `step_quality_*`
    (PREDICATE_CHECK reporta `all_satisfied`, no `passed`/`findings`). Devuelve
    "sin verificar" en vez de omitir: un placeholder sin proveedor rompe el
    contrato de los templates, y un silencio se lee como verde.
    """
    for execution in reversed(_executions(state)):
        data = _execution_data(execution)
        if "all_satisfied" in data:
            return "true" if data.get("all_satisfied") else "false"
    return "sin verificar"


def spec_context(spec: dict[str, Any] | None) -> dict[str, Any]:
    """Datos de la tarea para `task_table` / `risk_table` / `predicates`."""
    if not spec:
        return {}
    out: dict[str, Any] = {}
    rows = [
        f"Tarea: {spec.get('spec_id', '')} · tipo: {spec.get('spec_type', '')} · "
        f"dominio: {spec.get('domain') or '(sin dominio)'} · estado: {spec.get('status', '')}",
        f"Objetivo: {spec.get('objective') or '(sin objetivo)'}",
    ]
    problem = str(spec.get("problem_statement") or "").strip()
    if problem:
        rows.append(f"Problema: {problem}")
    files = spec.get("files") or []
    if files:
        rows.append("Archivos:\n" + "\n".join(f"  - {f}" for f in files[:_LIST_LIMIT]))
    acceptance = spec.get("acceptance_criteria") or []
    if acceptance:
        rows.append(
            "Criterios:\n" + "\n".join(f"  - {a}" for a in acceptance[:_LIST_LIMIT])
        )
    out["task_table"] = "\n".join(rows)

    risks = spec.get("risks")
    risk_items: list[str] = []
    if isinstance(risks, dict):
        risk_items = [f"{k}: {v}" for k, v in risks.items() if v]
    elif isinstance(risks, list):
        risk_items = [str(r) for r in risks if r]
    out["risk_table"] = (
        "Riesgos:\n" + "\n".join(f"  - {r}" for r in risk_items[:_LIST_LIMIT])
        if risk_items
        else "Riesgos: (ninguno declarado)"
    )

    items = _predicate_items(spec.get("predicates"))
    if items:
        out["predicates"] = "\n".join(f"  - {p}" for p in items[:_LIST_LIMIT])

    # F1a: inyectar el bloque reuse declarado en el WorkContract (advisory).
    tc = spec.get("task_context") or {}
    if isinstance(tc, dict):
        contract = tc.get("work_contract") or {}
        if isinstance(contract, dict):
            reuse = contract.get("reuse")
            if reuse:
                out["reuse"] = (
                    reuse if isinstance(reuse, str) else "Reuse declarado: " + str(reuse)
                )
    return out


def _predicate_items(predicates: Any) -> list[str]:
    items: list[str] = []
    if isinstance(predicates, dict):
        raw = predicates.get("items") or []
    elif isinstance(predicates, list):
        raw = predicates
    else:
        raw = []
    for predicate in raw:
        if isinstance(predicate, dict):
            items.append(
                f"{predicate.get('type')}: "
                f"{predicate.get('path') or predicate.get('target') or ''}"
            )
        elif predicate:
            items.append(str(predicate))
    return items


def decisions_context(chain_dir: str, task_id: str) -> dict[str, Any]:
    """`decisions_list`: decisiones del CKG (de la tarea si se puede filtrar)."""
    try:
        from fast_mcp.composition import ckg_for

        graph = ckg_for(chain_dir)
        if graph is None:
            return {}
        nodes = graph.query(_DECISION_FILTERS)
        # Una sola query: filtrar en Python evita el fallback EAGER por
        # `metadata.spec_id` (materializa todo el read model y loguea).
        if task_id:
            scoped = [
                n for n in nodes
                if str((n.get("metadata") or {}).get("spec_id") or "") == task_id
            ]
            nodes = scoped or nodes
        # 5, no 10: en un gate de decisión las primeras cinco decisiones son
        # las que restringen; el resto es ruido que empuja la pregunta lejos.
        lines = []
        for node in (nodes or [])[:5]:
            node_id = node.get("_id") or node.get("id") or ""
            label = str(node.get("label") or node.get("statement") or "")[:100]
            lines.append(f"  - {node_id}: {label}".rstrip(": "))
        return {"decisions_list": "\n".join(lines)} if lines else {}
    except Exception as exc:  # noqa: BLE001 — el briefing no depende del CKG
        logger.debug("prompt_context: decisiones no disponibles (%s)", exc)
        return {}


def reflection_context(
    chain_dir: str,
    task_id: str,
    failed_gates: str = "",
    last_error: str = "",
) -> dict[str, Any]:
    """Variables de REFINE desde el TraceGraph (paquete de reflexión)."""
    try:
        from fast_mcp.composition import build_trace_service

        service = build_trace_service(chain_dir)
        if service is None:
            return {}
        package = service.build_reflection_context(
            chain_dir,
            task_id=task_id,
            last_error=last_error,
            failed_gates=failed_gates,
        )
    except Exception as exc:  # noqa: BLE001 — el briefing no depende del trace
        logger.debug("prompt_context: reflexión no disponible (%s)", exc)
        return {}
    if not isinstance(package, dict) or not package:
        return {}
    past = package.get("past_attempts") or []
    formatted = "\n".join(f"  - {str(p)[:160]}" for p in past[-5:])
    return {
        "source_node": package.get("source_node", ""),
        "attempt": package.get("attempt", ""),
        "max_attempts": package.get("max_attempts", ""),
        "error_context": package.get("error_context", ""),
        "failed_gates": package.get("failed_gates", ""),
        "hint": package.get("hint", ""),
        "reflection_past_attempts": formatted,
    }


def _sprint_summary_item(spec: dict) -> str:
    """Bloque multilínea de una spec para el resumen del sprint."""
    spec_id = str(spec.get("spec_id") or "?")
    status = str(spec.get("status") or "")
    objective = str(spec.get("objective") or "")
    files = [str(f) for f in (spec.get("files") or [])]
    criteria = [str(c) for c in (spec.get("acceptance_criteria") or [])]
    depends = [str(d) for d in (spec.get("depends_on") or [])]
    lines = [f"- {spec_id} [{status}]: {objective}"]
    if files:
        lines.append("    archivos: " + ", ".join(files))
    if criteria:
        lines.append("    criterios: " + "; ".join(criteria))
    if depends:
        lines.append("    depende de: " + ", ".join(depends))
    return "\n".join(lines)


def sprint_extra(specs: list[dict], spring: dict, task_id: str) -> dict[str, Any]:
    """Datos del sprint y de la spec para los templates de SPRINT/IMPLEMENT."""
    in_flow = ("specified", "approved")
    ready = [s for s in specs if str(s.get("status")) in in_flow and not s.get("depends_on")]
    blocked = [s for s in specs if str(s.get("status")) in in_flow and s.get("depends_on")]
    extra: dict[str, Any] = {
        "sprint_goal": spring.get("goal", ""),
        "goal_display": f"Goal: {spring.get('goal', '')}",
        "task_count": len(specs),
        "completed_count": sum(1 for s in specs if str(s.get("status")) == "completed"),
        "ready_tasks": "\n".join(
            f"- {s.get('spec_id')}: {str(s.get('objective', ''))[:70]}"
            for s in ready[:_LIST_LIMIT]
        )
        or "(ninguna)",
        "blocked_tasks": "\n".join(
            f"- {s.get('spec_id')}: depende de {s.get('depends_on')}"
            for s in blocked[:_LIST_LIMIT]
        )
        or "(ninguna)",
        # Resumen del sprint COMPLETO al cerrar el plan: todas las specs, su
        # goal (objective), estado, archivos y criterios. Antes ACTIVE solo
        # mostraba objetivos truncados a 70 chars y el usuario no veía el plan
        # aprobado (medido 2026-11-24).
        "sprint_summary_list": "\n".join(
            _sprint_summary_item(s) for s in specs[:_LIST_LIMIT]
        )
        or "(sin specs)",
        "task_id": task_id,
    }
    spec = next((s for s in specs if str(s.get("spec_id")) == task_id), None)
    if spec:
        extra.update(
            {
                "task_title": spec.get("objective", ""),
                "task_description": spec.get("problem_statement")
                or spec.get("objective", ""),
                "task_type": spec.get("spec_type", ""),
                "task_domain": spec.get("domain", ""),
                "task_status": spec.get("status", ""),
                "result_state": spec.get("result_state", ""),
                "depends_on": ", ".join(spec.get("depends_on") or []) or "(ninguna)",
                "acceptance_criteria_list": "\n".join(
                    f"- {x}" for x in (spec.get("acceptance_criteria") or [])
                ),
                "files_list": "\n".join(f"- {x}" for x in (spec.get("files") or [])),
                "preconditions_list": "\n".join(
                    f"- {x}" for x in (spec.get("preconditions") or [])
                ),
                "quality_gates_list": "\n".join(
                    f"- {x}" for x in (spec.get("quality_gates") or [])
                ),
                "predicates_list": "\n".join(
                    f"- {p}" for p in _predicate_items(spec.get("predicates"))
                ),
            }
        )
        extra.update(spec_context(spec))
    return extra


def project_briefing_context(state: Any, chain_dir: str) -> dict[str, Any]:
    """Datos del proyecto para los gates de START y PROJECT_INIT.

    Medido 2026-11-18: `PROJECT_INIT.REVIEW` renderizaba `Name:`, `Language:`,
    `Framework:` y los conteos de CPG/CKG en blanco; `START.LIST` perdía
    `{count}`. Nadie proveía esos placeholders, así que el usuario aprobaba un
    proyecto que no veía. Se calculan solo para esos dos nodos: materializar el
    CKG en cada prompt sería caro.
    """
    node = str(getattr(state, "current_node", "") or "").upper()
    if node not in ("START", "PROJECT_INIT"):
        return {}

    out: dict[str, Any] = {}
    context = getattr(state, "context", {}) or {}
    project_id = str(context.get("project_id") or "") if isinstance(context, dict) else ""

    # Registro de proyectos: count + nombre.
    try:
        from fast_mcp.shared_kernel.registry_store import RegistryStore

        registry = RegistryStore().read()
        projects = registry.get("projects", {}) or {}
        out["count"] = len(projects)
        if not project_id:
            for pid, record in projects.items():
                if str(record.get("repo_path") or "") == str(chain_dir):
                    project_id = str(pid)
                    break
        record = projects.get(project_id) or {}
        out["project_name"] = str(record.get("name") or Path(chain_dir).name)
    except Exception as exc:  # noqa: BLE001 — el briefing no depende del registry
        logger.debug("prompt_context: registry no disponible (%s)", exc)
        out.setdefault("count", 0)
        out.setdefault("project_name", Path(chain_dir).name)

    # Lenguaje/framework por marcadores (heurística barata, sin importar tasks).
    root = Path(chain_dir)
    language, framework = "unknown", "no detectado"
    try:
        if (root / "pyproject.toml").is_file():
            language = "python"
            text = (root / "pyproject.toml").read_text(errors="ignore").lower()
            for candidate in ("fastapi", "django", "flask", "pytest"):
                if candidate in text:
                    framework = candidate
                    break
        elif (root / "package.json").is_file():
            language = "javascript"
            import json as _json

            pkg = _json.loads((root / "package.json").read_text(errors="ignore") or "{}")
            deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
            for candidate in ("next", "react", "vue", "svelte", "express"):
                if candidate in deps:
                    framework = candidate
                    break
        elif (root / "go.mod").is_file():
            language = "go"
        elif (root / "Cargo.toml").is_file():
            language = "rust"
    except Exception as exc:  # noqa: BLE001 — la detección no bloquea el gate
        logger.debug("prompt_context: detección de lenguaje falló (%s)", exc)
    out["language"] = language
    out["framework"] = framework

    # Estado del CPG/CKG (lo que el usuario necesita para aprobar).
    try:
        from fast_mcp.memories.context import MemoryContext

        mem = MemoryContext.resolve(chain_dir)
        if mem is not None:
            cpg = mem.cpg()
            stats = cpg.stats() if cpg is not None else {}
            out["cpg_nodes"] = int(stats.get("total_nodes") or 0)
            out["cpg_edges"] = int(stats.get("total_edges") or 0)
            ckg = mem.ckg()
            out["ckg_nodes"] = len(ckg.query({})) if ckg is not None else 0
    except Exception as exc:  # noqa: BLE001 — los conteos no bloquean el gate
        logger.debug("prompt_context: conteos CPG/CKG no disponibles (%s)", exc)
        out.setdefault("cpg_nodes", 0)
        out.setdefault("cpg_edges", 0)
        out.setdefault("ckg_nodes", 0)

    return out


def submitted_context(state: Any) -> dict[str, Any]:
    """Campos que el agente YA envió en sub-estados del nodo actual.

    Los prompts de gate declaran `{q1}..{a3}` y `{preconditions_list}`, pero el
    servicio de sprint no los guarda en la spec: viven en `sub_state_data` del
    workflow. Sin este puente, ANALYZE.REVIEW se renderizaba con las respuestas
    del 6->3 en blanco y el PO decidía a ciegas.

    Se fusionan los sub-estados en orden; el último valor gana.
    """
    node = str(getattr(state, "current_node", "") or "").upper()
    if node not in ("ANALYZE", "DESIGN", "REFINE"):
        return {}
    data = getattr(state, "sub_state_data", {}) or {}
    if not isinstance(data, dict):
        return {}

    # `sub_state_data[node]` es un `SubStateData` en runtime y un dict cuando
    # viene de JSON: se aceptan las dos formas.
    entry = data.get(node)
    fields = getattr(entry, "fields", None)
    if fields is None and isinstance(entry, dict):
        fields = entry.get("fields")
    merged = dict(fields) if isinstance(fields, dict) else {}

    out: dict[str, Any] = {
        key: merged[key]
        for key in ("q1", "a1", "q2", "a2", "q3", "a3")
        if str(merged.get(key) or "").strip()
    }
    preconditions = merged.get("preconditions")
    if isinstance(preconditions, list) and preconditions:
        out["preconditions"] = [str(x) for x in preconditions if str(x).strip()]
    for risk in ("curvature", "pre_churn", "commit_trust"):
        value = merged.get(risk)
        if value not in (None, ""):
            out[risk] = value
    return out


def session_plan_context(plan: dict[str, Any] | None) -> dict[str, Any]:
    """Lo que la sesión YA declaró, para que el primer nodo no pregunte de cero.

    `implement_task` escribe el vuelo (goal, acceptance, task, scope, next) antes
    de que el agente toque un nodo. Si el workflow no lo lee, su primera pregunta
    es "elegí un proyecto" mientras el trabajo ya tiene criterios de aceptación.
    Devuelve `{}` cuando no hay plan: no se inventa contexto.
    """
    if not isinstance(plan, dict):
        return {}
    task = str(plan.get("task") or "").strip()
    if not task:
        return {}
    acceptance = [str(a) for a in (plan.get("acceptance") or []) if str(a).strip()]
    scope = [str(s) for s in (plan.get("scope") or []) if str(s).strip()]
    out: dict[str, Any] = {
        "task": task,
        "goal": str(plan.get("goal") or ""),
        "acceptance": acceptance[:5],
        "scope": scope[:5],
        "next": str(plan.get("next") or ""),
    }
    # Línea ya renderizada: los templates usan sustitución simple (`<< x >>`),
    # así que un dict no se puede iterar. Se manda el texto listo y vacío cuando
    # no hay nada que declarar (el template queda igual).
    criteria = " · ".join(f"{i + 1}) {a[:60]}" for i, a in enumerate(out["acceptance"]))
    parts = [f"Esta sesión YA declaró la tarea `{task}`."]
    if criteria:
        parts.append(f"Criterios: {criteria}.")
    if out["next"]:
        parts.append(f"Próximo: {out['next']}.")
    parts.append(
        "No vuelvas a preguntar lo que ya está declarado: si el usuario confirma, "
        "respondé con intent=project_selected y project_id=<id>."
    )
    out["line"] = " ".join(parts)
    return {"existing_work": out, "existing_work_line": out["line"], "task_id": task}


def sprint_deliverable_context(state: Any) -> dict[str, Any]:
    """Informe y retro REGISTRADOS (SPRINT.REPORT/RETRO) como texto para el prompt.

    Sin esto, CLOSE_CONFIRM pedía "mostrá el resumen" pero no tenía el informe
    que el PO acababa de aprobar: el agente lo repetía de memoria o directamente
    no lo mostraba (medido 2026-11-27). El informe viaja tal cual se registró.
    """
    raw = getattr(state, "sub_state_data", None)
    if raw is None and isinstance(state, dict):
        raw = state.get("sub_state_data")
    data = raw if isinstance(raw, dict) else {}

    def _fields(key: str) -> dict:
        block = _as_dict(data.get(key))
        return _as_dict(block.get("fields") or block)

    report = _fields("REPORT")
    retro = _fields("RETRO")
    if not report and not retro:
        return {}

    lines: list[str] = []
    summary = str(report.get("summary") or "").strip()
    if summary:
        lines.append(summary)
    metrics = report.get("metrics")
    if isinstance(metrics, dict) and metrics:
        lines.extend(["", "MÉTRICAS"])
        for key, value in metrics.items():
            if isinstance(value, (list, tuple)):
                value = ", ".join(str(v) for v in value)
            lines.append(f"- {key}: {value}")
    for label, key in (("RIESGOS / DEUDA", "risks"), ("PRÓXIMOS PASOS", "next")):
        items = report.get(key)
        if isinstance(items, (list, tuple)) and items:
            lines.extend(["", label])
            lines.extend(f"- {item}" for item in items)
    lessons = retro.get("lecciones")
    if isinstance(lessons, (list, tuple)) and lessons:
        lines.extend(["", "RETRO — LECCIONES"])
        for item in lessons:
            text = item.get("leccion") if isinstance(item, dict) else item
            if text:
                lines.append(f"- {text}")
    return {"sprint_report": "\n".join(lines).strip()}


def build_prompt_extra(state: Any, chain_dir: str, session_id: str) -> dict[str, Any]:
    """Todo el `extra_data` que los templates pueden usar, sin inventar nada."""
    extra: dict[str, Any] = {}
    extra.update(quality_context(state))
    extra.update(sprint_deliverable_context(state))
    extra.update(project_briefing_context(state, chain_dir))
    extra["step_predicates"] = predicate_verdict(state)
    happened = last_step_summary(state)
    if happened:
        extra["what_happened"] = happened

    task_id = ""
    try:
        from fast_mcp.composition import build_sprint_service, sprint_context_for

        ctx_data = getattr(state, "context", {}) or {}
        context = ctx_data if isinstance(ctx_data, dict) else {}
        task_id = str(
            context.get("task_id")
            or getattr(state, "task_id", "")
            or ""
        )

        # Un solo SprintService por prompt (brief ya es caro, AE-INV-05).
        try:
            service = build_sprint_service(chain_dir)
        except Exception:  # noqa: BLE001 — el prompt no depende del delta
            service = None

        # Política ÚNICA (ADR-WALKER-085): sesión > activo > planning. Antes el
        # prompt caía a `get_spring_id(chain_dir)` = basename del proyecto, que
        # no es un sprint: pintaba `Goal:` vacío y `0/0` aunque el DeltaGraph
        # tuviera el sprint y sus specs.
        resolve = getattr(service, "resolve_context", None)
        requested = str(context.get("spring_id") or context.get("sprint_id") or "")
        if callable(resolve):
            sctx = resolve(chain_dir, session_id=session_id, spring_id=requested)
        else:
            sctx = sprint_context_for(
                chain_dir, session_id=session_id, spring_id=requested
            )
        if sctx.found:
            extra.update(sprint_extra(list(sctx.specs), sctx.spring, task_id))
            extra["sprint_id"] = sctx.spring_id
            extra["sprint_resolved_by"] = sctx.resolved_by
            if sctx.diagnostics:
                extra["sprint_diagnostics"] = list(sctx.diagnostics)

        # F5/P2: el primer nodo arranca desde lo que la sesión ya declaró.
        # `existing_work` viaja al prompt y `task_id` prefiere el plan de la
        # SESIÓN sobre el del proyecto: sin esto, START preguntaba "elegí un
        # proyecto" y el prompt citaba la spec de otra tarea (medido:
        # workflow_state devolvía SPEC-014 con la sesión trabajando en otra).
        plan_ctx: dict[str, Any] = {}
        if session_id and service is not None:
            try:
                plan_ctx = session_plan_context(service.get_session_plan(session_id))
            except Exception as exc:  # noqa: BLE001
                logger.debug("prompt_context: plan de sesión no disponible (%s)", exc)
        if plan_ctx:
            extra.update(plan_ctx)
            task_id = str(plan_ctx.get("task_id") or "") or task_id
    except Exception as exc:  # noqa: BLE001 — el prompt no depende del sprint
        logger.debug("prompt_context: sprint no disponible (%s)", exc)

    # El análisis 6->3 y las precondiciones que el agente ya envió. La spec no
    # los guarda (los escribe `transform_spec` al cerrar), así que sin esto el
    # gate de revisión mostraba las preguntas sin respuestas.
    submitted = submitted_context(state)
    extra.update({k: v for k, v in submitted.items() if k != "preconditions"})
    if not extra.get("preconditions_list") and submitted.get("preconditions"):
        extra["preconditions_list"] = "\n".join(
            f"- {x}" for x in submitted["preconditions"]
        )
    for risk in ("curvature", "pre_churn", "commit_trust"):
        if not str(extra.get(risk) or "").strip():
            extra[risk] = submitted.get(risk, "no declarado")

    if task_id:
        extra.update(decisions_context(chain_dir, task_id))

    # La reflexión del TraceGraph (source_node/attempt/error_context/hint) solo
    # la usan los prompts de REFINE. Calcularla en cada paso costaba cargar
    # 1.300 eventos por llamada a `workflow_state` sin que nadie la leyera.
    if str(getattr(state, "current_node", "") or "").upper() == "REFINE":
        extra.update(
            reflection_context(
                chain_dir,
                task_id,
                failed_gates=str(extra.get("step_failed_gates") or ""),
            )
        )
    return extra
