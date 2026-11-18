"""handlers/implement.py — implement_task + run_tests handlers.

Ported from mcp/shared/tasks/actions/implement.py (114 LOC → ~60 LOC).
Simplified: removed legacy ValidatedVO, uses plain dict validation.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult

# R1: tasks no importa delta_tracking. El composition root inyecta la factory
# en `handlers._di` (la comparten implement y verify).
from fast_mcp.features.tasks.handlers import _di

logger = logging.getLogger(__name__)


def configure(sprint_factory) -> None:
    _di.configure_sprint(sprint_factory)
from fast_mcp.features.tasks.handlers._inspect_codebase_helper import run_inspect_codebase


def _select_spring(service, session_override: str) -> dict:
    """Sprint de la sesión > activo > primero > SPR-ADHOC."""
    springs = service.list_springs() or []

    def _owns_session(s: dict) -> bool:
        if not session_override:
            return False
        if str(s.get("session_id") or "") == session_override:
            return True
        return session_override in ((s.get("context") or {}).get("session_ids") or [])

    spring = next((s for s in springs if _owns_session(s)), None)
    if not spring:
        spring = service.get_active_spring()
    if not spring and springs:
        spring = springs[0]
    if not spring:
        spring = service.create_spring("SPR-ADHOC", goal="tareas declaradas en sesión")
    return spring


def _ensure_active(service, spring: dict | None, resume: bool) -> tuple[bool, str, dict | None]:
    """ADR-WALKER-079: declarar tarea en un sprint PAUSADO se rechaza, salvo resume.

    Reanudar implícitamente haría que `paused` signifique "nadie declaró nada
    desde la última pausa": una garantía débil y silenciosa. El rechazo es
    explícito y accionable; `resume: true` es el opt-in del llamador.
    """
    if not spring:
        return True, "", spring
    if str(spring.get("status") or "") != "paused":
        return True, "", spring
    spring_id = str(spring.get("spring_id") or spring.get("id") or "")
    if resume:
        result = service.transition_spring(spring_id, "active")
        if not result.get("ok"):
            return False, f"no se pudo reanudar {spring_id}: {result.get('error')}", spring
        return True, "", service.get_spring(spring_id) or {**spring, "status": "active"}
    return (
        False,
        f"el sprint {spring_id} está PAUSADO. Reanimalo con sprint_status "
        f'\'{{"spring_id":"{spring_id}","status":"active"}}\' o repetí con resume:true.',
        spring,
    )


def _persist_spec(
    ctx: ExecutionContext,
    task_id: str,
    objective: str,
    files: list[str],
    data: dict,
    session_override: str = "",
    spring: dict | None = None,
) -> dict | None:
    """Crea o actualiza la spec de la tarea en el delta."""
    if not ctx.chain_dir:
        return None
    service = _di.sprint_service(ctx.chain_dir)
    if service is None:
        return None

    spring = spring or _select_spring(service, session_override)
    spring_id = str(spring.get("spring_id") or spring.get("id") or "")
    if not spring_id:
        return None

    from fast_mcp.features.tasks.handlers.spec_derive import (
        criteria_to_predicates,
        derive,
        predicates_from_files,
    )

    derived = derive(objective or "")
    mapped = criteria_to_predicates(derived["acceptance_criteria"], ctx.chain_dir)
    if not mapped["predicates"]:
        # Sin criterios en el texto (títulos descriptivos), los ARCHIVOS dicen
        # cómo verificar: correr los tests del área tocada.
        mapped = predicates_from_files(files, ctx.chain_dir)
    # Validación VISIBLE: no bloquea (una tarea sin criterios es un estado real),
    # pero queda registrada en la spec para que el hueco no sea silencioso.
    spec_errors = _di.validate_spec({
        "spec_id": task_id or "?",
        "objective": objective or task_id,
        "files": files,
        "predicates": {"items": mapped["predicates"]},
        "acceptance_criteria": derived["acceptance_criteria"],
        "status": "in_progress",
    })

    base = {
        "objective": objective or task_id or "tarea sin objetivo",
        "files": files,
        "status": "in_progress",
        "acceptance_criteria": derived["acceptance_criteria"],
        "preconditions": derived["preconditions"],
        "predicates": {
            "items": mapped["predicates"],
            "unmapped": mapped["unmapped"],
        },
        "task_context": {
            "task_id": task_id,
            "session_id": session_override or ctx.session_id or "",
            "derived": derived["source"],
            "validation": {"ok": not spec_errors, "errors": spec_errors},
        },
    }

    # Si ya existe una spec de esta tarea, se actualiza (no se duplica).
    for spec in service.get_specs(spring_id) or []:
        ctx_data = spec.get("task_context") or {}
        if task_id and (ctx_data.get("task_id") == task_id or spec.get("objective") == objective):
            updated = service.update_spec(spring_id, str(spec.get("spec_id")), base)
            return updated or spec

    created = service.add_spec(
        spring_id,
        objective=base["objective"],
        files=files,
        spec_type=str(data.get("spec_type", "fix") or "fix"),
        status="in_progress",
        description=str(data.get("description", "") or ""),
    )
    if created:
        return _attach_task_context(
            service, spring_id, task_id, session_override or ctx.session_id, files, objective,
            criteria=base["acceptance_criteria"],
            preconditions=base["preconditions"],
            predicates=base["predicates"],
            validation=base["task_context"]["validation"],
            derived=derived["source"],
        )
    return _attach_task_context(service, spring_id, task_id, ctx.session_id, files, objective)


def _attach_task_context(
    service, spring_id, task_id, session_id, files, objective,
    criteria: list[str] | None = None,
    preconditions: list[str] | None = None,
    predicates: dict | None = None,
    validation: dict | None = None,
    derived: str = "empty",
) -> dict | None:
    """`add_spec` no acepta `task_context` ni criterios: se completan con un update."""
    specs = service.get_specs(spring_id) or []
    updates = {
        "task_context": {
            "task_id": task_id,
            "session_id": session_id or "",
            "derived": derived,
            "validation": validation or {},
        },
        "files": files,
        "acceptance_criteria": criteria or [],
        "preconditions": preconditions or [],
        "predicates": predicates or {},
    }
    for spec in reversed(specs):
        if str(spec.get("objective", "")) == (objective or task_id or "tarea sin objetivo"):
            return service.update_spec(spring_id, str(spec.get("spec_id")), updates) or spec
    return specs[-1] if specs else None


def implement_task(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Prepare task implementation — records objective and files.

    W5 (ADR-076): si ctx.cpg_adapter está disponible y `data` incluye
    `inspect_codebase_action`, ejecuta la action y añade el resultado.
    """
    objective = data.get("objective", "")
    files = data.get("files", [])
    if not isinstance(files, list):
        files = []

    task_id = str(data.get("task_id") or ctx.task_id or "").strip()
    # La sesión REAL viene del caller (el plugin la conoce); `ctx.session_id`
    # en el path de tool es "mcp" y no sirve para unir delta ↔ trace ↔ CKG.
    session_override = str(data.get("session_id", "") or "").strip()

    # La política de pausa se aplica SOLO si se puede resolver el sprint. Antes
    # esta resolución vivía dentro del try de `_persist_spec`: sacarla hizo que
    # un chain_dir sin proyecto abortara la declaración (regresión medida).
    chosen = None
    service = None
    try:
        service = _di.sprint_service(ctx.chain_dir)
        chosen = _select_spring(service, session_override) if service is not None else None
        ok, msg, chosen = _ensure_active(service, chosen, bool(data.get("resume")))
        if not ok:
            return TaskResult.failure(f"implement_task: {msg}")
    except Exception as exc:  # noqa: BLE001
        chosen = None
        logger.debug("implement_task: sin sprint para la política de pausa (%s)", exc)

    result: dict[str, Any] = {
        "task_id": task_id,
        "result_state": objective,
        "files_modified": files,
    }

    # Persistir la tarea en el delta: es la ÚNICA forma de que el contexto del
    # agente pueda anclarse en la tarea (delta → archivos → CPG → CKG). Antes
    # sólo devolvía un dict y el delta quedaba vacío para siempre.
    try:
        spec = _persist_spec(ctx, task_id, objective, files, data, session_override, spring=chosen)
        if task_id:
            # Puente al workflow: a partir de acá toda acción recibe el id.
            _di.record_task(ctx.chain_dir, task_id)
        if spec:
            result["spec"] = {"spec_id": spec.get("spec_id"), "status": spec.get("status")}
    except Exception as exc:  # noqa: BLE001 — fail-loud en el dato, no en la tool
        result["spec_error"] = f"{type(exc).__name__}: {exc}"

    # Plan de vuelo: la tarea y el alcance declarados son lo que el agente
    # debe mantener a la vista (ADR-WALKER-080).
    if service is not None and session_override:
        try:
            service.update_session_plan(
                session_override,
                {"task": task_id, "scope": files, "next": "implementar y verificar"},
            )
        except Exception as exc:  # noqa: BLE001
            result["plan_error"] = f"{type(exc).__name__}: {exc}"

    inspect_result = run_inspect_codebase(ctx.cpg_adapter, data)
    if inspect_result is not None:
        result["inspect_codebase"] = inspect_result

    return TaskResult.success(data=result)


def _resolve_project_root(chain_dir: str) -> str:
    """Walk up from chain_dir to find project root (dir with tests/)."""
    current = os.path.abspath(chain_dir)
    while True:
        if os.path.isdir(os.path.join(current, "tests")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return chain_dir
        current = parent


def run_tests(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Run test suite via pytest --collect-only.

    SPEC-014: prohibits false positive (passed=True with total=0).
    """
    project_root = _resolve_project_root(ctx.chain_dir or "")
    try:
        result = subprocess.run(
            ["pytest", "--collect-only", "-q"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return TaskResult.failure(
                f"run_tests: pytest --collect-only failed (rc={result.returncode})"
            )
        match = re.search(r"(\d+) tests? collected", result.stderr + result.stdout)
        total = int(match.group(1)) if match else 0
        if total <= 0:
            return TaskResult.failure(
                "run_tests: 0 tests collected — cannot certify QA"
            )
        return TaskResult.success(data={
            "task_id": data.get("task_id", ctx.task_id),
            "passed": True,
            "total": total,
            "failures": 0,
            "runner": "pytest --collect-only",
        })
    except subprocess.TimeoutExpired:
        return TaskResult.failure("run_tests: pytest timed out (120s)")
    except FileNotFoundError:
        return TaskResult.failure("run_tests: pytest not found")
    except Exception as exc:
        return TaskResult.failure(f"run_tests: {type(exc).__name__}: {exc}")


def register(registry) -> None:
    """Register implement_task and run_tests."""
    registry.register("implement_task", implement_task, metadata={
        "description": "Prepare task implementation",
        "tags": ["implementation"],
        "scope": "internal",
    })
    registry.register("run_tests", run_tests, metadata={
        "description": "Run test suite",
        "tags": ["test"],
        "scope": "internal",
    })
