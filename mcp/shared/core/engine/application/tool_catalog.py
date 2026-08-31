"""engine/application/tool_catalog.py — Catálogo público de tools y rutas.

Expone tools/rutas del ModuleRegistry sin necesidad de instanciar el
McpServer. Evita circular imports entre mcp_adapter y CLI.

Usado por:
  - MCP: tools/list, resources/list
  - CLI: help, list

Composición (SPEC-005 #8): el registry canónico de tasks públicas se construye
UNA vez por proceso (singleton) — cero registries efímeros por llamada.
"""
from __future__ import annotations

import logging
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)

# Cache del registry canónico de tasks públicas: {registry}
_TASK_CATALOG_CACHE: dict = {"system_registry": None}


def _canonical_system_registry(task_registry: Any = None) -> Any:
    """Registry canónico de tasks públicas (SSOT de visibilidad).

    Resuelve el system_registry del composition root único (capa_rout.md D1,
    ADR-045): separa el task_registry canónico registrado por features/main
    como servicio DI. Si el composition root no está poblado, lanza KeyError
    — la rotura de composición es VISIBLE (los wrappers list_tools/get_tool
    la capturan con warning, convention-001), nunca un catálogo vacío mudo.

    Se cachea por instancia (los registries son singletons).
    """
    global _TASK_CATALOG_CACHE

    cached = _TASK_CATALOG_CACHE
    if cached["system_registry"] is None:
        from shared.core.engine.infrastructure.system_task_registry import (
            SystemTaskRegistry,
        )

        if task_registry is None:
            # Fail-open (convention-001): un registry inyectado sin el servicio
            # `task_registry` (p. ej. registries de solo-módulos en tests) NO
            # debe romper el catálogo de tools del ModuleRegistry. Se loggea el
            # warning para diagnóstico y se construye un system_registry vacío.
            logger.warning(
                "tool_catalog: task_registry no inyectado; "
                "el catálogo solo expondrá tools del ModuleRegistry"
            )
            system_registry = SystemTaskRegistry(tracer=None)
            cached["system_registry"] = system_registry
            return system_registry
        canonical = task_registry
        system_registry = SystemTaskRegistry(tracer=getattr(canonical, "_tracer", None))
        for name, fn in canonical._tasks.items():
            meta = canonical._metadata.get(name, {})
            scope = meta.get("scope", "public")
            if scope not in ("internal", "domain"):
                system_registry.register(name, fn, meta)
        cached["system_registry"] = system_registry
    return cached["system_registry"]


def _resolve_task_registry(registry: Any) -> Any | None:
    """Resuelve el task_registry canónico desde el ModuleRegistry inyectado (DI).

    ADR-045 / capa_rout.md D1: el task_registry se registra como servicio DI en
    el composition root (features/main), accesible vía
    `module_registry.get_service("task_registry")`. El mismo `registry` que ya
    se inyecta en list_tools() es el ModuleRegistry, así que lo resolvemos de ahí
    en lugar de importar features/main (respeta la frontera core/engine).

    Fail-open (convention-001): si no está disponible, devuelve None y
    _canonical_system_registry lo señala con warning en lugar de catálogo mudo.
    """
    if registry is None:
        return None
    get_service = getattr(registry, "get_service", None)
    if callable(get_service):
        try:
            return get_service("task_registry")
        except Exception:
            return None
    return None


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
    depender del composition root. Default: get_module_registry() (facade
    neutro de core/engine, poblado por features/main — ADR-045).

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
    if registry is None:
        raise ValueError("list_tools: registry no inyectado (composition root required)")
    registry = registry
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
    #    Registry canónico cacheado por mtime (SPEC-005 #8). El task_registry
    #    canónico se resuelve desde el ModuleRegistry inyectado (DI), no se
    #    importa features/main (frontera core/engine).
    task_registry = _resolve_task_registry(registry)
    public = _canonical_system_registry(task_registry=task_registry).discover(scope="public") or {"tasks": []}
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


def list_routes(registry: Any = None) -> list[dict]:
    """Lista todas las rutas REST registradas en los módulos.

    Result pattern (convention-001): fail-open con log, nunca lanza.
    """
    outcome = _list_routes_core(registry=registry)
    if is_successful(outcome):
        return outcome.value_or([])
    error = outcome.failure()
    logger.warning(
        "tool_catalog.list_routes fallo (%s: %s); rutas vacias",
        type(error).__name__, error,
    )
    return []


@safe
def _list_routes_core(registry: Any = None) -> list[dict]:
    """Core puro de list_routes (puede lanzar)."""
    if registry is None:
        raise ValueError("list_routes: registry no inyectado (composition root required)")
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
    depender del composition root. Default: get_module_registry().
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
    if registry is None:
        raise ValueError("get_tool: registry no inyectado (composition root required)")
    registry = registry
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


def get_route(name: str, registry: Any = None) -> dict | None:
    """Obtiene una ruta por nombre de tool.

    Result pattern (convention-001): fail-open con log, nunca lanza.
    """
    outcome = _get_route_core(name, registry=registry)
    if is_successful(outcome):
        return outcome.value_or(None)
    error = outcome.failure()
    logger.warning(
        "tool_catalog.get_route('%s') fallo (%s: %s)",
        name, type(error).__name__, error,
    )
    return None


@safe
def _get_route_core(name: str, registry: Any = None) -> dict | None:
    """Core puro de get_route (puede lanzar)."""
    if registry is None:
        raise ValueError("get_route: registry no inyectado (composition root required)")
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
