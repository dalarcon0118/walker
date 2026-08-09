"""delta_analyze task — API pública unificada del DeltaGraph.

Sub-comandos soportados:
  walker <project>/delta_analyze '{"action":"context"}'
  walker <project>/delta_analyze '{"action":"create_spring","goal":"..."}'
  walker <project>/delta_analyze '{"action":"capture","objective":"..."}'
  walker <project>/delta_analyze '{"action":"list_springs"}'
  walker <project>/delta_analyze '{"action":"activate","spring_id":"..."}'
  walker <project>/delta_analyze '{"action":"close","spring_id":"..."}'
  walker <project>/delta_analyze '{"action":"specs","spring_id":"..."}'
  walker <project>/delta_analyze '{"action":"complete_spec","spring_id":"...","spec_id":"..."}'
  walker <project>/delta_analyze '{"action":"import_legacy","sprint_id":"..."}'
  walker <project>/delta_analyze '{"action":"detect_legacy"}'

Retorna siempre el mismo envelope: {ok, action, result, next_step}

Result pattern (dry-python/returns): los cores puros con @safe nunca lanzan;
el wrapper decide con is_successful y hace fallback explícito con log
(convention-001: nunca except silencioso).
"""
from __future__ import annotations
import logging
from typing import Any
from returns.pipeline import is_successful
from returns.result import safe
from libs.shared_domain import ExecutionContext, TaskResult

logger = logging.getLogger(__name__)


@safe
def _resolve_default_port(chain_dir: str) -> Any:
    """Core puro: resuelve el adaptador Delta (puede lanzar)."""
    from features.delta_tracking.infrastructure.adapters.walker_delta_adapter import WalkerDeltaAdapter
    from features.delta_tracking.application.ports.delta_repository import DeltaRepository
    from features.delta_tracking.infrastructure.cognitive_delta_repo import CognitiveDeltaRepository
    from features.project import ProjectService

    paths = ProjectService.resolve_paths(chain_dir)
    data_dir = str(paths.project_root)
    repo = CognitiveDeltaRepository(data_dir)
    return WalkerDeltaAdapter(repo)


@safe
def _dispatch_action(port: Any, action: str, data: dict[str, Any], chain_dir: str) -> dict:
    """Core puro: despacha la acción al port (puede lanzar)."""
    result: dict[str, Any] = {"action": action}
    if action == "context":
        result["result"] = port.get_context(data.get("spring_id", ""))
    elif action == "create_spring":
        result["result"] = port.create_spring(
            goal=data.get("goal", ""),
            project_id=data.get("_project_id", ""),
            spring_id=data.get("spring_id", ""),
            display_name=data.get("display_name", ""),
        )
    elif action == "capture":
        result["result"] = port.capture_task(data)
    elif action == "list_springs":
        result["result"] = port.list_springs(data.get("status", ""))
    elif action == "activate":
        result["result"] = port.activate_spring(data.get("spring_id", ""))
    elif action == "close":
        result["result"] = port.close_spring(data.get("spring_id", ""))
    elif action == "specs":
        result["result"] = port.get_specs(data.get("spring_id", ""), data.get("status", ""))
    elif action == "create_spec":
        spring_id = data.get("spring_id", "")
        if not spring_id:
            active = port.get_active_spring()
            if active:
                spring_id = active.get("spring_id", "")
        result["result"] = port.create_spec(
            spring_id=spring_id,
            objective=data.get("objective", ""),
            domain=data.get("domain", ""),
            files=data.get("files", []),
        )
    elif action == "update_spec":
        result["result"] = port.update_spec(
            data.get("spring_id", ""),
            data.get("spec_id", ""),
            data.get("changes", data),
        )
    elif action == "complete_spec":
        result["result"] = port.complete_spec(
            data.get("spring_id", ""),
            data.get("spec_id", ""),
            data.get("result_state", ""),
        )
    elif action == "import_legacy":
        from features.delta_tracking.application.use_cases.import_legacy import ImportLegacyHistoryUseCase
        import_result = ImportLegacyHistoryUseCase.execute(chain_dir, data.get("sprint_id", ""))
        result["result"] = import_result.value_or({}) if hasattr(import_result, "value_or") else import_result
    elif action == "detect_legacy":
        from features.delta_tracking.application.use_cases.import_legacy import DetectLegacyUseCase
        detect_result = DetectLegacyUseCase.execute(chain_dir)
        result["result"] = detect_result.value_or([]) if hasattr(detect_result, "value_or") else detect_result
    else:
        result["result"] = port.get_context(action)
    return result


def delta_analyze(
    ctx: ExecutionContext,
    data: dict[str, Any],
    port: Any | None = None,
) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available", data={"next_step": "Run 'walker prepare <path>' first"})

    # Resolver port con Result pattern: fallback explícito con log (convention-001).
    if port is None:
        resolved = _resolve_default_port(chain_dir)
        if is_successful(resolved):
            port = resolved.value_or(None)
        else:
            logger.warning(
                "delta_analyze: no se pudo resolver el port Delta (%s: %s)",
                type(resolved.failure()).__name__, resolved.failure(),
            )
            port = None
        if port is None:
            return TaskResult.failure("DeltaServicePort not available", data={"next_step": "Check delta_tracking feature"})

    action = data.get("action", "context")
    dispatched = _dispatch_action(port, action, data, chain_dir)
    if not is_successful(dispatched):
        error = dispatched.failure()
        logger.warning(
            "delta_analyze action '%s' fallo (%s: %s)",
            action, type(error).__name__, error,
        )
        return TaskResult.failure(f"delta_analyze failed: {error}", data={"action": action})

    result = dispatched.value_or({"action": action})
    result.setdefault("next_step", "Use 'action' to refine: context, create_spring, capture, list_springs, activate, close, specs, create_spec, update_spec, complete_spec, import_legacy, detect_legacy")
    return TaskResult.success(data=result)


def register(registry: Any) -> None:
    registry.register(
        "delta_analyze",
        delta_analyze,
        metadata={
            "description": "Unified DeltaGraph analysis: context, create_spring, capture, list_springs, activate, close, specs, create_spec, update_spec, complete_spec, import_legacy, detect_legacy",
            "usage": 'walker <project>/delta_analyze \'{"action":"context"}\'',
            "example": 'walker <project>/delta_analyze \'{"action":"create_spring","goal":"..."}\'',
            "tags": ["delta", "unified", "analysis"],
            "scope": "internal",
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "Sub-comando: context, create_spring, capture, list_springs, activate, close, specs, create_spec, update_spec, complete_spec, import_legacy, detect_legacy (default context)", "example": "create_spring"},
                    "spring_id": {"type": "string", "description": "ID del spring (create_spring, activate, close, specs, create_spec, update_spec, complete_spec)", "example": "sp-001"},
                    "goal": {"type": "string", "description": "Objetivo del spring (action=create_spring)", "example": "Implementar auth"},
                    "display_name": {"type": "string", "description": "Nombre visible del spring (action=create_spring)", "example": "Spring Auth"},
                    "domain": {"type": "string", "description": "Dominio de la spec (action=create_spec)", "example": "auth"},
                    "objective": {"type": "string", "description": "Descripción/objetivo de la spec (action=create_spec)", "example": "Endpoint /login"},
                    "status": {"type": "string", "description": "Filtro de estado (action=list_springs, specs)", "example": "active"},
                    "files": {"type": "array", "description": "Archivos asociados a la spec (action=create_spec)", "example": ["src/auth.py"]},
                    "spec_id": {"type": "string", "description": "ID de la spec (action=update_spec, complete_spec)", "example": "sp-001"},
                    "changes": {"type": "object", "description": "Cambios/actualizaciones de la spec (action=update_spec)", "example": {"status": "done"}},
                    "result_state": {"type": "string", "description": "Estado resultante al completar spec (action=complete_spec)", "example": "done"},
                    "sprint_id": {"type": "string", "description": "ID del sprint legacy (action=import_legacy)", "example": "legacy-7"},
                },
                "required": [],
            },
        },
    )
