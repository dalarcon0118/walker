"""inspect_codebase task — análisis integral del código del proyecto.

Concepto de negocio: inspeccionar la base de código para entender
arquitectura, detectar código muerto, revisar seguridad, ver métricas
o construir el índice CPG. Unifica los sub-comandos legacy
(cpg_analyze / update_cpg / scan_code_and_build_cpg) bajo un nombre
de operación de negocio.

Result pattern (dry-python/returns): los cores con @safe nunca lanzan;
el wrapper decide con is_successful y hace fallback explícito + log
(convention-001: nunca except silencioso).
"""
from __future__ import annotations

import logging
from typing import Any
from returns.pipeline import is_successful
from returns.result import safe
from shared.core.engine.domain.execution import ExecutionContext, TaskResult

logger = logging.getLogger(__name__)


@safe
def _resolve_default_port(chain_dir: str) -> Any:
    """Core puro: resuelve el port de análisis CPG (puede lanzar)."""
    from tasks.application.cpg_port_resolver import resolve_cpg_port
    return resolve_cpg_port(chain_dir)


def _resolve_project_root_from_chain_dir(chain_dir: str) -> str:
    """Resuelve el project_root real desde el chain_dir del sprint."""
    try:
        from features.project import ProjectService
        paths = ProjectService.resolve_paths(chain_dir)
        return str(paths.project_root)
    except Exception:
        return chain_dir


def _effective_scope(scope: str, chain_dir: str) -> str:
    """Deriva el scope efectivo: usa el scope del usuario si es un path real;
    si no, cae a project_root para no escanear archivos JSON del sprint."""
    if not scope:
        return _resolve_project_root_from_chain_dir(chain_dir)
    if scope == chain_dir:
        return _resolve_project_root_from_chain_dir(chain_dir)
    return scope


@safe
def _dispatch_action(
    port: Any,
    action: str,
    scope: str,
    chain_dir: str,
) -> dict:
    """Core puro: despacha la acción al port (puede lanzar)."""
    result: dict[str, Any] = {"scope": scope, "action": action}
    target = _effective_scope(scope, chain_dir)

    if action == "business":
        result["result"] = port.business_layer(focus=target)
    elif action == "dead_code":
        result["result"] = port.detect_unused_functions(file_filter=target)
    elif action == "security":
        result["result"] = port.scan_dfg(action="start", project_root=target)
        result["result"]["action"] = "security_scan"
    elif action == "review":
        result["result"] = port.review_code()
    elif action == "metrics":
        result["result"] = port.project_metrics(file_filter=target)
    elif action == "build":
        result["result"] = port.build_cpg(project_root=target)
    elif action == "update":
        # Sync incremental via watcher: solo archivos cambiados (build_service.update).
        # Si el puerto no soporta update, fallback a build con warning VISIBLE
        # en el resultado (antes se tragaba en un logger.warning invisible).
        if hasattr(port, "update"):
            try:
                result["result"] = port.update(project_root=target)
            except TypeError:
                # Puertos legacy con update() sin kwargs
                result["result"] = port.update()
            result["action"] = "update"
            # Auto-ensenanza: el agente que llamo sin conocer force descubre
            # aqui la via de escape al rebuild completo (parametro muerto -> vivo).
            result["mode"] = "incremental"
            result["next_step"] = (
                'Si sospechas drift o necesitas re-scan total: '
                'walker <project>/build_code_index {"force": true}'
            )
        else:
            result["result"] = port.build_cpg(project_root=target)
            result["warning"] = (
                "puerto CPG sin update(); fallback a build completo "
                "(esperado solo en puertos legacy)"
            )
    elif action == "context":
        files = [scope] if scope else None
        result["result"] = port.gather_context(files=files)
    else:
        result["result"] = port.business_layer(focus=target)
    return result


def _coerce_scope(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and value:
        return str(value[0]).strip()
    return ""


def _detect_action(data: dict[str, Any]) -> str:
    # Override explícito: permite pedir acciones no cubiertas por flags
    # legacy (p.ej. action="update" para el sync incremental del watcher).
    explicit = data.get("action")
    if isinstance(explicit, str) and explicit:
        return explicit
    if data.get("detect") == "dead_code":
        return "dead_code"
    if data.get("detect") == "security":
        return "security"
    if data.get("review"):
        return "review"
    if data.get("metrics"):
        return "metrics"
    if data.get("build"):
        return "build"
    if data.get("depth") == "business" or data.get("depth") == "business_layer":
        return "business"
    return "business"


def inspect_codebase(
    ctx: ExecutionContext,
    data: dict[str, Any],
    port: Any | None = None,
) -> TaskResult:
    """Task canónica: inspecciona el código del proyecto."""
    # Resolución de contexto (principio explícito > ambiental): un
    # project_id en el payload GANA sobre el chain_dir del proceso. Esto es
    # lo que permite operar proyectos remotos desde un server MCP anclado a
    # otro cwd (multi-proyecto, regla 1b del protocolo).
    chain_dir = ctx.chain_dir or ""
    explicit_pid = (data.get("project_id") or data.get("_project_id") or "").strip()
    if explicit_pid:
        try:
            from features.project.application.services.project_service import (
                ProjectService,
            )

            _project = ProjectService.from_project_id(explicit_pid)
            chain_dir = str(_project.manifest.repo_path or "") or chain_dir
        except Exception as exc:
            logger.warning(
                "inspect_codebase: project_id '%s' no resolvió (%s); "
                "se usa contexto ambiental",
                explicit_pid,
                exc,
            )
    if not chain_dir:
        return TaskResult.failure(
            "chain_dir not available",
            data={"next_step": "Run 'walker prepare <path>' first"},
        )

    if port is None:
        resolved = _resolve_default_port(chain_dir)
        if is_successful(resolved):
            port = resolved.value_or(None)
        else:
            logger.warning(
                "inspect_codebase: no se pudo resolver el port CPG (%s: %s)",
                type(resolved.failure()).__name__,
                resolved.failure(),
            )
            return TaskResult.failure(
                "CPG port unavailable",
                data={"next_step": "Run 'walker prepare <path>' first"},
            )

    scope = _coerce_scope(data.get("scope", data.get("area", chain_dir)))
    action = _detect_action(data)
    dispatched = _dispatch_action(port, action, scope, chain_dir)
    if not is_successful(dispatched):
        error = dispatched.failure()
        logger.warning(
            "inspect_codebase action '%s' fallo (%s: %s)",
            action,
            type(error).__name__,
            error,
        )
        return TaskResult.failure(
            f"inspect_codebase failed: {error}",
            data={"scope": scope, "action": action},
        )

    result = dispatched.value_or({"scope": scope, "action": action})
    result.setdefault(
        "next_step",
        "Use 'scope' + 'detect'/'depth' to refine analysis",
    )
    return TaskResult.success(data=result)


def register(registry: Any) -> None:
    registry.register(
        "inspect_codebase",
        inspect_codebase,
        metadata={
            "description": "Inspecciona la base de código: arquitectura, código muerto, seguridad, métricas o índice CPG.",
            "usage": 'walker <project>/inspect_codebase \'{"scope":"<path>","depth":"business"}\'',
            "example": 'walker <project>/inspect_codebase \'{"scope":"src/","detect":"dead_code"}\'',
            "tags": ["code", "analysis", "inspection"],
            "scope": "public",
        },
    )
