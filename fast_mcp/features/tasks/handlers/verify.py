"""handlers/verify.py — verify_predicates handler (anti-hallucination, G-W6).

Uses the verify_engine from infrastructure/ for real predicate checking:
  - file_exists, symbol_exists, test_pass, command_exit, grep, etc.
  - Empty predicates → failure (never empty success, G-W6).
  - Shell commands, AST-level verification, batch optimization.
"""
from __future__ import annotations

import logging
import re
import subprocess
from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult
from fast_mcp.features.tasks.infrastructure.verify_engine import (
    invalidate_verify_cache,
    verify_predicates_batch,
)
from fast_mcp.features.tasks.handlers._inspect_codebase_helper import run_inspect_codebase

logger = logging.getLogger(__name__)

# AE-INV-12 / P3c: un check no puede certificarse con reglas que el mismo cambio
# tocó. Se detectan los archivos de regla que el predicado va a ejecutar
# (`tests/test_x.py`, `spec/...`), y si están MODIFICADOS respecto de HEAD el
# verde queda `weak`. Modo `observe` por defecto; `strict=true` lo vuelve rojo.
_RULE_PATH_RE = re.compile(
    r"(?:^|[\s'\"=(])((?:[\w.-]+/)*(?:tests?|spec|specs)/[\w./-]+\.(?:py|js|mjs|cjs|ts|tsx))"
)
_MAX_RULES = 20


def _rule_files(predicates: list[Any]) -> list[str]:
    """Archivos de regla que los predicados ejecutan (no los que verifican).

    Sólo `test_pass` y `command_exit` ejecutan código: `file_exists`,
    `symbol_exists` o `grep` leen el árbol pero no corren una regla. Se extraen
    del comando, sin adivinar: si el path no aparece, no hay regla que cuidar.
    """
    found: list[str] = []
    for pred in predicates:
        if not isinstance(pred, dict):
            continue
        if str(pred.get("type") or "") not in ("test_pass", "command_exit"):
            continue
        command = str(pred.get("command") or "")
        for match in _RULE_PATH_RE.finditer(command):
            path = match.group(1).lstrip("./")
            if path not in found:
                found.append(path)
    return found[:_MAX_RULES]


def _dirty_rule_files(project_root: str, rules: list[str]) -> tuple[list[str], str]:
    """Reglas modificadas respecto de HEAD.

    Devuelve `(archivos, estado)` con estado `ok|no-rules|no-git|git-error`.
    Un archivo NUEVO no cuenta: agregar un test no cambia la regla que certifica
    el trabajo, modificarla sí. `git diff --name-only HEAD` no lista untracked.
    """
    if not rules:
        return [], "no-rules"
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", "HEAD", "--", *rules],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"git-error: {type(exc).__name__}"
    if out.returncode != 0:
        return [], "no-git"
    dirty = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    return dirty, "ok"



def verify_predicates(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Verify predicates against real code using the verify engine.

    G-W6 anti-hallucination: empty predicates → failure.

    W5 (ADR-076): si ctx.cpg_adapter está disponible y `data` incluye
    `inspect_codebase_action`, ejecuta la action y añade el resultado.
    """
    if data.get("_fresh"):
        invalidate_verify_cache()

    predicates = data.get("predicates", [])
    if not isinstance(predicates, list):
        predicates = []

    project_root = data.get("project_root") or ctx.chain_dir or ""
    if not project_root:
        return TaskResult.failure(
            "verify_predicates: no project_root/chain_dir — cannot verify"
        )

    # Evidencia de escritura para el flight state (P1.12): el diff contra el
    # último scan, tomado ANTES de ejecutar predicados (que pueden escribir).
    from fast_mcp.features.tasks.handlers.verification import changed_since_last_scan

    touched = changed_since_last_scan(
        project_root,
        session_id=str(
            data.get("session_id") or getattr(ctx, "session_id", "") or ""
        ),
    )

    # Sin predicados del caller, se leen de la SPEC de la tarea en curso: el
    # workflow ya los derivó (`spec.predicates.items`) y nadie los estaba
    # usando. El gate era fail-loud con razón, pero el dato existía.
    predicates_source = "caller" if predicates else ""
    unmapped: list[dict] = []
    if not predicates:
        try:
            from fast_mcp.features.tasks.handlers import _di

            _sid = str(data.get("session_id") or getattr(ctx, "session_id", "") or "")
            _spring = _di.session_spring(ctx.chain_dir or "", _sid) if _sid else ""
            try:
                spec = _di.current_spec(
                    ctx.chain_dir or "",
                    task_id=str(data.get("task_id", "") or ""),
                    spring_id=_spring,
                    exact=True,
                )
            except TypeError:
                spec = _di.current_spec(
                    ctx.chain_dir or "",
                    task_id=str(data.get("task_id", "") or ""),
                )
            if spec:
                bundle = spec.get("predicates") or {}
                predicates = [p for p in (bundle.get("items") or []) if isinstance(p, dict)]
                unmapped = list(bundle.get("unmapped") or [])
                if predicates:
                    predicates_source = f"spec:{spec.get('spec_id')}"
        except Exception as exc:  # noqa: BLE001 — sin spec se sigue al fail-loud
            unmapped = [{"reason": f"no se pudo leer la spec ({type(exc).__name__})"}]

    # G-W6: empty predicates = no verification criteria = failure
    if not predicates:
        detalle = ""
        if unmapped:
            detalle = " La spec derivó criterios pero ninguno es ejecutable: " + "; ".join(
                str(u.get("reason", "")) for u in unmapped[:3]
            )
        return TaskResult.failure(
            "verify_predicates: predicates vacíos — "
            "el gate no puede validar nada. Inyecta task.validation.predicates "
            "desde la spec (DeltaGraph) o define predicates en la spec." + detalle
        )

    results = verify_predicates_batch(predicates, project_root)

    detailed = []
    failed = 0
    for pred, (passed, info) in zip(predicates, results):
        if not passed:
            failed += 1
        detailed.append({
            "predicate": info.get("label") or (pred if isinstance(pred, str) else str(pred)),
            "method": info.get("method", "unknown"),
            "passed": passed,
            "detail": info,
        })

    result_data = {
        "task_id": data.get("task_id") or ctx.task_id,
        "all_satisfied": failed == 0,
        "results": detailed,
        "failed_count": failed,
        "passed_count": len(detailed) - failed,
        "total": len(detailed),
        # De dónde salieron los predicados y qué criterios quedaron sin chequeo
        # automático: sin esto el gate parece completo cuando no lo está.
        "predicates_source": predicates_source or "caller",
        "unmapped": unmapped,
    }

    # P3c (AE-INV-12): ¿el verde se certificó con reglas que este mismo cambio
    # modificó? Detección contra HEAD. `strict` convierte la debilidad en rojo;
    # por defecto es `observe` (se marca, no se bloquea) para no frenar trabajo
    # legítimo sobre tests antes de tener la tasa de falsos positivos.
    rules = _rule_files(predicates)
    dirty_rules, rules_check = _dirty_rule_files(project_root, rules)
    strict = bool(data.get("strict"))
    result_data["self_certified"] = bool(dirty_rules)
    result_data["dirty_rules"] = dirty_rules
    result_data["rules_check"] = rules_check
    result_data["strict"] = strict
    if dirty_rules:
        result_data["weak"] = True
        if strict:
            result_data["all_satisfied"] = False
            result_data["failed_count"] = result_data.get("failed_count", 0) + 1
            result_data["total"] = result_data.get("total", 0) + 1
            result_data["results"].append({
                "predicate": "checks:no_self_certified",
                "method": "rules_from_head",
                "passed": False,
                "detail": {
                    "dirty_rules": dirty_rules,
                    "reason": "la verificación ejecuta reglas modificadas por este cambio",
                },
            })

    # `getattr`: el motor arma un ctx mínimo; asumir el atributo rompía el
    # paso entero con AttributeError en vez de degradar la capability opcional.
    inspect_result = run_inspect_codebase(getattr(ctx, "cpg_adapter", None), data)
    if inspect_result is not None:
        result_data["inspect_codebase"] = inspect_result
        # W6 (ADR-076): action=imports detecta imports alucinados;
        # si hay Hallucinations, fallar el predicado con el listado.
        if data.get("inspect_codebase_action") == "imports":
            hallucinations = inspect_result.get("hallucinations", 0)
            if hallucinations > 0:
                result_data["all_satisfied"] = False
                result_data["hallucinations_found"] = True
                result_data["failed_count"] = result_data.get("failed_count", 0) + hallucinations
                result_data["total"] = result_data.get("total", 0) + hallucinations
                result_data["results"].append({
                    "predicate": "imports:no_hallucinations",
                    "method": "hallucination_detector",
                    "passed": False,
                    "detail": {
                        "hallucinations": hallucinations,
                        "issues": inspect_result.get("issues", [])[:10],
                    },
                })

    # P1.11: la verificación queda en el TRACE; el plugin la hidrata aunque no
    # haya visto el comando. Se registra después de `inspect_codebase` para que
    # un hallazgo de alucinaciones cuente como rojo.
    from fast_mcp.features.tasks.handlers.verification import record_verification

    record_verification(
        ctx,
        session_id=str(data.get("session_id") or getattr(ctx, "session_id", "") or ""),
        passed=bool(result_data.get("all_satisfied")),
        level="predicates",
        runner="verify_predicates",
        task_id=str(data.get("task_id") or getattr(ctx, "task_id", "") or ""),
        touched=touched,
        weak=bool(dirty_rules),
        # SPEC-021: el nodo de evidencia lleva los contadores reales (2/3), no
        # sólo "verde/rojo".
        counts={
            "passed": int(result_data.get("passed_count") or 0),
            "failed": int(result_data.get("failed_count") or 0),
            "total": int(result_data.get("total") or 0),
        },
    )

    return TaskResult.success(data=result_data)


def register(registry) -> None:
    """Register verify_predicates."""
    registry.register("verify_predicates", verify_predicates, metadata={
        "description": "Verify task predicates against code (anti-hallucination)",
        "tags": ["verification"],
        "scope": "internal",
    })
