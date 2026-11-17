"""handlers/baseline.py — acciones `capture_baseline` y `diff_baseline`.

Son el punto A y el punto B del workflow, ejecutados por el MOTOR (sub-estados
`execution: auto`), no por el agente: una foto del estado no requiere criterio y
pedirla por prompt se ignora.

- `capture_baseline` corre al entrar en ANALYZE (después de sincronizar el
  índice): estructura, relaciones, complejidad, archivos, convenciones y
  defectos del scope.
- `diff_baseline` corre en VERIFY, después de los predicados: qué cambió
  respecto de la foto tomada al empezar la tarea.
"""
from __future__ import annotations

from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult

_DIMENSIONS_RESUMEN = ("structure", "relations", "complexity", "files")

# R1: esta feature no importa otra feature. El composition root inyecta una
# factory `chain_dir -> BaselineService` al registrar las acciones.
_FACTORY = None


def configure(baseline_factory) -> None:
    """Inyecta la factory del servicio de baseline (la llama el composition root)."""
    global _FACTORY
    _FACTORY = baseline_factory


def _service(chain_dir: str):
    if _FACTORY is None:
        raise RuntimeError(
            "baseline: factory no inyectada (el composition root debe llamar "
            "handlers.baseline.configure)"
        )
    return _FACTORY(chain_dir)


def _label_for(data: dict, ctx: ExecutionContext) -> str:
    """Label estable entre `capture_baseline` y `diff_baseline`.

    OJO: `ctx.task_id` llega con el NOMBRE DE LA ACCIÓN cuando el workflow no
    declara uno (`capture_baseline` / `diff_baseline`), así que usarlo producía
    labels distintos y el diff nunca encontraba su base. Solo se acepta un
    task_id explícito en `data`; si no, se usa el label fijo de la tarea en
    curso.
    """
    explicit = str(data.get("label", "") or "").strip()
    if explicit:
        return explicit
    # NO usar data["task_id"]: el orquestador lo rellena con el nombre de la
    # ACCIÓN cuando el workflow no declara uno, así que capture y diff recibían
    # labels distintos y el diff nunca encontraba su base.
    return "task-actual"


def capture_baseline(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Toma la foto del estado actual y la persiste con label por tarea."""
    if not ctx.chain_dir:
        return TaskResult.failure("capture_baseline: sin chain_dir")

    label = _label_for(data, ctx)
    scope = str(data.get("scope", "") or "")
    try:
        snap = _service(ctx.chain_dir).capture(scope=scope, label=label)
    except Exception as exc:  # noqa: BLE001 — fail-loud, nunca silencio
        return TaskResult.failure(f"capture_baseline falló: {type(exc).__name__}: {exc}")

    return TaskResult.success(data={
        "baseline": label,
        "scope": scope,
        "dimensions": snap.get("dimensions"),
        **{k: snap.get(k) for k in _DIMENSIONS_RESUMEN},
        "conventions": snap.get("conventions"),
        "defects": snap.get("defects"),
    })


def diff_baseline(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Compara la foto tomada al empezar contra el estado actual."""
    if not ctx.chain_dir:
        return TaskResult.failure("diff_baseline: sin chain_dir")

    label = _label_for(data, ctx)
    scope = str(data.get("scope", "") or "")
    try:
        result = _service(ctx.chain_dir).diff(label=label, scope=scope)
    except Exception as exc:  # noqa: BLE001
        return TaskResult.failure(f"diff_baseline falló: {type(exc).__name__}: {exc}")

    if result.get("error"):
        # Sin foto previa no hay punto A: se reporta, no se inventa un diff vacío.
        return TaskResult.failure(f"diff_baseline: {result['error']}")

    return TaskResult.success(data={
        "baseline": label,
        "summary": result.get("summary"),
        "changes": result.get("changes"),
    })


def register(registry) -> None:
    """Registra las dos acciones en el TaskRegistry."""
    registry.register("capture_baseline", capture_baseline, metadata={
        "description": "BASELINE: foto del estado (estructura, relaciones, complejidad, archivos)",
        "tags": ["baseline", "preflight"],
        "scope": "internal",
    })
    registry.register("diff_baseline", diff_baseline, metadata={
        "description": "BASELINE: diff contra la foto tomada al empezar la tarea",
        "tags": ["baseline", "verify"],
        "scope": "internal",
    })
