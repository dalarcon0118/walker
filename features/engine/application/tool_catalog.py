"""engine/application/tool_catalog.py — Catálogo público de tools y rutas.

Expone tools/rutas del ModuleRegistry sin necesidad de instanciar el
McpServer. Evita circular imports entre mcp_adapter y CLI.

Usado por:
  - MCP: tools/list, resources/list
  - CLI: help, list

Composición (SPEC-005 #8): el registry canónico de tasks públicas se construye
UNA vez por proceso (singleton con invalidez por mtime del paquete de tasks,
patrón reloader) — cero registries efímeros por llamada.
"""
from __future__ import annotations

import logging
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)

# Cache del registry canónico de tasks públicas: {sig_mtime, system_registry}
_TASK_CATALOG_CACHE: dict = {"sig": None, "system_registry": None}


@safe
def _task_package_signature_core() -> float:
    """Core puro: firma mtime del paquete de tasks (puede lanzar OSError)."""
    import os

    import features.task_execution.tasks as _tasks_pkg

    tasks_dir = os.path.dirname(os.path.abspath(_tasks_pkg.__file__))
    sig = 0.0
    for fname in os.listdir(tasks_dir):
        if fname.endswith(".py"):
            sig = max(sig, os.path.getmtime(os.path.join(tasks_dir, fname)))
    return sig


def _canonical_system_registry() -> Any:
    """Registry canónico de tasks públicas (SSOT de visibilidad).

    Consume bootstrap(separated=True) (capa_rout.md D1): el system_registry
    del composition root único — ya NO se construye un InMemoryTaskRegistry
    efímero por llamada (antes sin tracer y sin side effects, desincronizado
    del canónico). Se cachea por firma mtime del paquete de tasks (los .py
    cambian de mtime al registrar una task nueva o editar un fix, invalidando
    el cache como el reloader de _SERVER). Result pattern: ante fallo de
    firma se usa -1.0 (rebuild forzado).
    """
    global _TASK_CATALOG_CACHE
    from features.engine.bootstrap import bootstrap

    sig = _task_package_signature_core().value_or(-1.0)
    cached = _TASK_CATALOG_CACHE
    if cached["sig"] != sig or cached["system_registry"] is None:
        _, system_registry, _ = bootstrap(separated=True)
        cached["system_registry"] = system_registry
        cached["sig"] = sig
    return cached["system_registry"]


def _valid_input_schema(schema: dict | None) -> dict:
    """Normaliza un inputSchema a un JSON Schema object válido.

    Los clientes MCP estrictos (p. ej. opencode) rechazan tools cuyo
    inputSchema no tenga `type: "object"` y `properties` (dict) — un schema
    vacío `{}` o `{"type": "object"}` sin properties rompe `tools/list` y el
    server se marca como "Failed to get tools". Es la frontera de serialización
    SSOT: TODO inputSchema que llegue aquí se emite como object válido.
    """
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return {"type": "object", "properties": {}}
    if not isinstance(schema.get("properties"), dict):
        schema = dict(schema)
        schema["properties"] = {}
    return schema


def list_tools(registry: Any = None) -> list[dict]:
    """Lista todas las tools MCP registradas en los módulos.

    `registry` opcional: permite inyectar un ModuleRegistry (tests/CLI) sin
    depender del singleton de la app. Default: bootstrap_modules().

    FUSIÓN SSOT (ADR-043): combina
      1) tools del ModuleRegistry (workflow/manuales: status, submit, ...) — su
         `output_schema` vive en el ToolDefinition.
      2) tasks públicas del TaskRegistry (kg_*, agent_context, cpg_analyze, ...) —
         su output_schema/input_schema vive en la metadata del registro.
    Así `tools/list` expone AMBAS fuentes con su outputSchema (el gap que dejaba
    las tools de fase fuera y sin contrato de salida).
    """
    outcome = _list_tools_core(registry)
    if is_successful(outcome):
        return outcome.value_or([])
    error = outcome.failure()
    logger.warning(
        "tool_catalog.list_tools fallo (%s: %s); catalogo vacio",
        type(error).__name__, error,
    )
    return []


@safe
def _list_tools_core(registry: Any) -> list[dict]:
    """Core puro de list_tools: fusiona ModuleRegistry + tasks públicas (puede lanzar)."""
    from features.engine.bootstrap import bootstrap_modules

    registry = registry or bootstrap_modules()
    tools = []
    seen = set()

    # 1) Módulos composite (workflow manual tools)
    for tool in registry.all_tools():
        if tool.name in seen:
            continue
        seen.add(tool.name)
        tools.append({
            "name": tool.name,
            "description": tool.description,
            "inputSchema": _valid_input_schema(tool.input_schema),
            "outputSchema": tool.output_schema or {},
            "metadata": tool.metadata,
        })

    # 2) Tasks públicas del registro (kg_*, analyze, phase tools, ...)
    #    Registry canónico cacheado por mtime (SPEC-005 #8).
    public = _canonical_system_registry().discover(scope="public") or {"tasks": []}
    for t in public.get("tasks", []):
        name = t.get("name", "")
        if not name or name in seen:
            continue
        seen.add(name)
        tools.append({
            "name": name,
            "description": t.get("description", f"Public task: {name}"),
            "inputSchema": _valid_input_schema(t.get("input_schema")),
            "outputSchema": t.get("output_schema") or {},
            "metadata": {k: v for k, v in t.items() if k not in ("name", "description", "input_schema", "output_schema")},
        })
    return tools


def list_routes() -> list[dict]:
    """Lista todas las rutas REST registradas en los módulos.

    Result pattern (convention-001): fail-open con log, nunca lanza.
    """
    outcome = _list_routes_core()
    if is_successful(outcome):
        return outcome.value_or([])
    error = outcome.failure()
    logger.warning(
        "tool_catalog.list_routes fallo (%s: %s); rutas vacias",
        type(error).__name__, error,
    )
    return []


@safe
def _list_routes_core() -> list[dict]:
    """Core puro de list_routes (puede lanzar)."""
    from features.engine.bootstrap import bootstrap_modules

    registry = bootstrap_modules()
    routes = []
    seen = set()
    for route in registry.all_routes():
        if route.path in seen:
            continue
        seen.add(route.path)
        routes.append({
            "path": route.path,
            "method": route.method,
        })
    return routes


def get_tool(name: str, registry: Any = None) -> dict | None:
    """Obtiene una tool por nombre.

    `registry` opcional: permite inyectar un ModuleRegistry (tests/CLI) sin
    depender del singleton de la app. Default: bootstrap_modules().
    """
    outcome = _get_tool_core(name, registry)
    if is_successful(outcome):
        return outcome.value_or(None)
    error = outcome.failure()
    logger.warning(
        "tool_catalog.get_tool('%s') fallo (%s: %s)",
        name, type(error).__name__, error,
    )
    return None


@safe
def _get_tool_core(name: str, registry: Any = None) -> dict | None:
    """Core puro de get_tool (puede lanzar)."""
    from features.engine.bootstrap import bootstrap_modules

    registry = registry or bootstrap_modules()
    tool = registry.tool(name)
    if tool is not None:
        return {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": _valid_input_schema(tool.input_schema),
            "outputSchema": tool.output_schema or {},
            "metadata": tool.metadata,
        }
    # Fallback: task pública del registro (misma fusión que list_tools).
    # SystemTaskRegistry filtra por metadata.scope (SSOT, SPEC-005 #7).
    info = _canonical_system_registry().get_task_info(name)
    if info is None:
        return None
    return {
        "name": info.get("name", name),
        "description": info.get("description", f"Public task: {name}"),
        "inputSchema": _valid_input_schema(info.get("input_schema")),
        "outputSchema": info.get("output_schema") or {},
        "metadata": {k: v for k, v in info.items() if k not in ("name", "description", "input_schema", "output_schema")},
    }


def get_route(name: str) -> dict | None:
    """Obtiene una ruta por nombre de tool.

    Result pattern (convention-001): fail-open con log, nunca lanza.
    """
    outcome = _get_route_core(name)
    if is_successful(outcome):
        return outcome.value_or(None)
    error = outcome.failure()
    logger.warning(
        "tool_catalog.get_route('%s') fallo (%s: %s)",
        name, type(error).__name__, error,
    )
    return None


@safe
def _get_route_core(name: str) -> dict | None:
    """Core puro de get_route (puede lanzar)."""
    from features.engine.bootstrap import bootstrap_modules

    registry = bootstrap_modules()
    route = registry.route(name)
    if route is None:
        return None
    return {
        "path": route.path,
        "method": route.method,
    }


def build_tool_help(name: str, registry: Any = None) -> dict | None:
    """Genera la doc del contrato de una tool (F4 SPEC-043 RF-C1).

    `walker://help/<tool>` se sirve DESDE la metadata del registro — nunca desde
    un texto fijo. Devuelve:

        {
          "name", "description",
          "example":   metadata.example (o ""),
          "usage":     metadata.usage (tareas públicas) o "(MCP tool)",
          "input_schema", "output_schema",
        }

    None si la tool no existe (fail como resource 404).
    """
    tool = get_tool(name, registry=registry)
    if tool is None:
        return None
    meta = tool.get("metadata") or {}
    return {
        "name": tool["name"],
        "description": tool.get("description", ""),
        "example": meta.get("example", ""),
        "usage": meta.get("usage", "(MCP tool)"),
        "input_schema": tool.get("inputSchema") or {},
        "output_schema": tool.get("outputSchema") or {},
    }
