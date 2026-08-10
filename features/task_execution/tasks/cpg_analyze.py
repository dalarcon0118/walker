"""cpg_analyze task — API pública unificada del CPG.

Sub-comandos soportados:
  scope + depth:
    walker <project>/cpg_analyze '{"scope":"src/auth","depth":"business"}'
  detect:
    walker <project>/cpg_analyze '{"scope":"src/","detect":"dead_code"}'
    walker <project>/cpg_analyze '{"scope":"src/payments","detect":"security"}'
  review:
    walker <project>/cpg_analyze '{"scope":"src/","review":true}'
  metrics:
    walker <project>/cpg_analyze '{"scope":"src/","metrics":true}'

Retorna siempre el mismo envelope: {ok, action, result, next_step}

Result pattern (dry-python/returns): los cores con @safe nunca lanzan; el
wrapper decide con is_successful y hace fallback explícito con log
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
    """Core puro: resuelve el port de análisis CPG (puede lanzar)."""
    from features.task_execution.application.cpg_port_resolver import resolve_cpg_port
    return resolve_cpg_port(chain_dir)


@safe
def _dispatch_action(port: Any, action: str, scope: str, chain_dir: str) -> dict:
    """Core puro: despacha la acción al port (puede lanzar)."""
    result: dict[str, Any] = {"scope": scope, "action": action}
    if action == "business":
        result["result"] = port.business_layer(focus=scope)
    elif action == "dead_code":
        result["result"] = port.detect_unused_functions(file_filter=scope)
    elif action == "security":
        result["result"] = port.scan_dfg(action="start", project_root=chain_dir)
        result["result"]["action"] = "security_scan"
    elif action == "review":
        result["result"] = port.review_code()
    elif action == "metrics":
        result["result"] = port.project_metrics(file_filter=scope)
    elif action == "build":
        # project_root debe ser el repo real de código (scope), no el chain_dir
        # (sprint dir): CPGBuilder necesita el repo para resolver manifest y
        # code_paths. scan_code_and_build_cpg pasa el repo real como scope.
        result["result"] = port.build_cpg(project_root=scope or chain_dir)
    elif action == "context":
        files = [scope] if scope else None
        result["result"] = port.gather_context(files=files)
    else:
        result["result"] = port.business_layer(focus=scope)
    return result


def cpg_analyze(
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
                "cpg_analyze: no se pudo resolver el port CPG (%s: %s)",
                type(resolved.failure()).__name__, resolved.failure(),
            )
            port = None
        if port is None:
            return TaskResult.failure("CpgAnalysisPort not available", data={"next_step": "Check code_analysis feature"})

    scope = _coerce_scope(data.get("scope", data.get("area", "")))
    action = _detect_action(data)

    dispatched = _dispatch_action(port, action, scope, chain_dir)
    if not is_successful(dispatched):
        error = dispatched.failure()
        logger.warning(
            "cpg_analyze action '%s' fallo (%s: %s)",
            action, type(error).__name__, error,
        )
        return TaskResult.failure(f"cpg_analyze failed: {error}", data={"scope": scope, "action": action})

    result = dispatched.value_or({"scope": scope, "action": action})
    result.setdefault("next_step", "Use 'scope' + 'detect'/'depth' to refine analysis")
    return TaskResult.success(data=result)


def _coerce_scope(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and value:
        return str(value[0]).strip()
    return ""


def _detect_action(data: dict[str, Any]) -> str:
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


def register(registry: Any) -> None:
    registry.register(
        "cpg_analyze",
        cpg_analyze,
        metadata={
            "description": "Unified CPG analysis: business layer, dead code, security, review, metrics, build, context",
            "usage": 'walker <project>/cpg_analyze \'{"scope":"<path>","depth":"business"}\'',
            "example": 'walker <project>/cpg_analyze \'{"scope":"src/","detect":"dead_code"}\'',
            "tags": ["cpg", "unified", "analysis"],
            "scope": "public",
        },
    )
