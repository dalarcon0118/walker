"""libs/mcp_server.py — McpServer: composite MCP protocol router (adapter fino).

Cada modulo del walker declara sus tools/resources via register(server).
McpServer compone todos los modulos y rutea JSON-RPC requests.

ARQUITECTURA COMPOSITE + DIP (ADR-WALKER-016):
    McpServer (libs, infra transversal) recibe UN ToolRegistry inyectado
    (features/cli_interface/domain/services.py) por constructor. NO importa
    features — usa duck-typing sobre la interfaz del registry.

    Composition root (_build_server en mcp_adapter):
        registry = ToolRegistry()       # dominio del Protocol
        server   = McpServer(registry)  # adapter fino

    McpServer.tool()      → delega en registry.register()      (SSOT)
    McpServer.list_tools()→ delega en registry.list_tools()    (SSOT)
    McpServer.call_tool() → delega en registry.dispatch()      (SSOT)
    McpServer.resource()  → delega en registry.register_resource()

    Si no se inyecta registry, crea uno interno (backward-compat).
"""

from __future__ import annotations

from typing import Any, Callable


class _InternalRegistry:
    """Registry mínimo interno (fallback si no se inyecta ToolRegistry)."""

    def __init__(self):
        self._tools: dict[str, dict] = {}
        self._resources: list[dict] = []

    def register(self, name, description, handler, input_schema=None, output_schema=None) -> tuple[Any, list[str]]:
        if name in self._tools:
            return None, [f"Tool '{name}' already registered (shadowing blocked)"]
        schema = input_schema or {"type": "object", "properties": {"query": {"type": "object"}}}
        entry = {
            "name": name, "description": description,
            "inputSchema": schema, "handler": handler,
        }
        if output_schema:
            entry["outputSchema"] = output_schema
        self._tools[name] = entry
        return self._tools[name], []

    def register_resource(self, uri_pattern, name, description, mime_type, handler) -> tuple[Any, list[str]]:
        self._resources.append({
            "uri": uri_pattern, "name": name,
            "description": description, "mimeType": mime_type, "handler": handler,
        })
        return self._resources[-1], []

    def list_tools(self) -> list[dict]:
        return [
            {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"],
             **({"outputSchema": t["outputSchema"]} if "outputSchema" in t else {})}
            for t in sorted(self._tools.values(), key=lambda x: x["name"])
        ]

    def list_resources(self) -> list[dict]:
        return [
            {"uri": r["uri"], "name": r["name"], "description": r["description"], "mimeType": r["mimeType"]}
            for r in self._resources
        ]

    def get_handler(self, name: str) -> Callable | None:
        t = self._tools.get(name)
        return t["handler"] if t else None

    def get_resource_handler(self, uri_pattern: str) -> Callable | None:
        for r in self._resources:
            if r["uri"] == uri_pattern:
                return r.get("handler")
        return None

    def dispatch(self, name: str, args: dict) -> tuple[dict, list[str]]:
        handler = self.get_handler(name)
        if handler is None:
            return {}, [f"Unknown tool '{name}'"]
        try:
            return handler(args), []
        except Exception as e:
            return {}, [str(e)]


class McpServer:
    """Composite MCP protocol server. Routes JSON-RPC requests to registered modules.

    Adapter fino sobre un ToolRegistry inyectado (DIP). Sin lógica de negocio.
    """

    def __init__(self, registry: Any | None = None, tracer: Any | None = None):
        self._registry = registry or _InternalRegistry()
        self._tracer = tracer  # inyectado (duck-typing: .trace(...)); libs no importa features
        self._prompts: list[dict] = []

    # ── Registration API ──

    def tool(
        self,
        name: str,
        description: str,
        handler: Callable[[dict], dict],
        input_schema: dict | None = None,
        output_schema: dict | None = None,
    ) -> tuple[bool, list[str]]:
        """Register a tool. Return-pattern: (ok, errors). Bloquea shadowing.

        Delega en el ToolRegistry (SSOT del schema del agente). `output_schema`
        (ADR-WALKER-043) se propaga igual que input_schema; si el registro no lo
        soporta, se ignora silenciosamente (compatibilidad).
        """
        if not name or not str(name).strip():
            return False, ["tool name cannot be empty"]
        result, errors = self._registry.register(
            name, description, handler, input_schema=input_schema, output_schema=output_schema
        )
        if errors:
            return False, errors
        return True, []

    def register_tools(self, registry: Any) -> tuple[int, list[str]]:
        """Adopta los tools de un ToolRegistry (domain) como SSOT unico.

        Cada tool del registry se re-registra via self.tool() con su schema.
        Returns (count, errors).
        """
        errors: list[str] = []
        count = 0
        for definition in registry.list_tools():
            ok, errs = self.tool(
                name=definition["name"],
                description=definition["description"],
                input_schema=definition["inputSchema"],
                output_schema=definition.get("outputSchema"),
                handler=registry.get_handler(definition["name"]),
            )
            if not ok:
                errors.extend(errs)
                continue
            count += 1
        return count, errors

    def resource(
        self,
        uri_pattern: str,
        name: str,
        description: str,
        handler: Callable[[dict], dict],
    ) -> None:
        """Register a dynamic resource with URI pattern.

        URI patterns support {param} placeholders.
        The handler receives {"uri": full_uri, "params": {extracted}}.
        """
        self._registry.register_resource(
            uri_pattern, name, description, "application/json", handler
        )

    def prompt(self, name: str, description: str) -> None:
        self._prompts.append({"name": name, "description": description})

    # ── Composition ──

    def compose(self, module_register: Callable[["McpServer"], None]) -> "McpServer":
        """Register all tools/resources from a module. Returns self for chaining."""
        module_register(self)
        return self

    # ── Read-only access (for JSON-RPC responses) ──

    def list_tools(self) -> list[dict]:
        return self._registry.list_tools()

    def list_resources(self) -> list[dict]:
        return self._registry.list_resources()

    def list_prompts(self) -> list[dict]:
        return self._prompts

    # ── Routing ──

    def call_tool(self, name: str, args: dict) -> dict:
        result, errors = self._registry.dispatch(name, args)
        self._publish_events()
        if errors:
            return {"error": errors[0]}
        return result

    def _publish_events(self) -> None:
        """Propaga los eventos del ToolRegistry al tracer inyectado (DIP).

        Los eventos del dominio del Protocol (ToolInvoked/ToolFailed) se
        consumen para observabilidad (TraceGraph). El tracer se inyecta por
        constructor — libs no importa features. Best-effort: si el tracer
        falla, no rompe el dispatch.
        """
        if self._tracer is None:
            return
        collect = getattr(self._registry, "collect_events", None)
        if not callable(collect):
            return
        events = collect()
        if not events:
            return
        trace_fn = getattr(self._tracer, "trace", None)
        if not callable(trace_fn):
            return
        for event in events:
            try:
                ev_type = type(event).__name__
                ev_data = getattr(event, "__dict__", {})
                trace_fn(
                    chain_dir=".",
                    event_type=ev_type,
                    data=ev_data,
                )
            except Exception:
                pass  # best-effort observability

    def read_resource(self, uri: str) -> dict:
        """Resolve a resource URI to its handler."""
        get_handler = getattr(self._registry, "get_resource_handler", None)
        for resource in self._registry.list_resources():
            pattern = resource.get("uri", "") if isinstance(resource, dict) else str(resource.uri_pattern)
            params = self._match_uri(pattern, uri)
            if params is not None:
                handler = get_handler(pattern) if callable(get_handler) else None
                if handler is not None:
                    return handler({"uri": uri, "params": params})
                # fallback: resource sin handler accesible
                return {"contents": [{"uri": uri, "mimeType": "application/json", "text": "{}"}]}
        return {"error": f"Resource '{uri}' not found"}

    @staticmethod
    def _match_uri(pattern: str, uri: str) -> dict | None:
        """Match URI pattern with {placeholders} against actual URI."""
        pattern_parts = pattern.split("/")
        uri_parts = uri.split("/")
        if len(pattern_parts) != len(uri_parts):
            return None
        params = {}
        for pp, up in zip(pattern_parts, uri_parts):
            if pp.startswith("{") and pp.endswith("}"):
                params[pp[1:-1]] = up
            elif pp != up:
                return None
        return params
