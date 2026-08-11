"""engine/application/tool_catalog.py — Catálogo público de tools y rutas.

Expone tools/rutas del ModuleRegistry sin necesidad de instanciar el
McpServer. Evita circular imports entre mcp_adapter y CLI.

Usado por:
  - MCP: tools/list, resources/list
  - CLI: help, list
"""
from __future__ import annotations


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
    try:
        from features.engine.bootstrap import bootstrap_modules, bootstrap_tasks
        from features.engine.infrastructure.task_registry import InMemoryTaskRegistry
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
        from ..infrastructure.task_registry import InMemoryTaskRegistry
        from ..infrastructure.system_task_registry import SystemTaskRegistry
        base_registry = InMemoryTaskRegistry(tracer=None)
        bootstrap_tasks(base_registry)
        system_registry = SystemTaskRegistry(base=base_registry)
        public = system_registry.discover(scope="public") or {"tasks": []}
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
    except Exception:
        return []


def list_routes() -> list[dict]:
    """Lista todas las rutas REST registradas en los módulos."""
    try:
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
    except Exception:
        return []


def get_tool(name: str, registry: Any = None) -> dict | None:
    """Obtiene una tool por nombre.

    `registry` opcional: permite inyectar un ModuleRegistry (tests/CLI) sin
    depender del singleton de la app. Default: bootstrap_modules().
    """
    try:
        from features.engine.bootstrap import bootstrap_modules, bootstrap_tasks
        from features.engine.infrastructure.task_registry import InMemoryTaskRegistry
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
        task_registry = InMemoryTaskRegistry(tracer=None)
        bootstrap_tasks(task_registry)
        info = task_registry.get_task_info(name)
        if info is None:
            return None
        return {
            "name": info.get("name", name),
            "description": info.get("description", f"Public task: {name}"),
            "inputSchema": _valid_input_schema(info.get("input_schema")),
            "outputSchema": info.get("output_schema") or {},
            "metadata": {k: v for k, v in info.items() if k not in ("name", "description", "input_schema", "output_schema")},
        }
    except Exception:
        return None


def get_route(name: str) -> dict | None:
    """Obtiene una ruta por nombre de tool."""
    try:
        from features.engine.bootstrap import bootstrap_modules
        registry = bootstrap_modules()
        route = registry.route(name)
        if route is None:
            return None
        return {
            "path": route.path,
            "method": route.method,
        }
    except Exception:
        return None


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
