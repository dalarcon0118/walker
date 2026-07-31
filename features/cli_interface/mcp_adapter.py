"""mcp_adapter.py — Adaptador de entrada: walker como servidor MCP.

ARQUITECTURA LIMPIA: este modulo es un ADAPTADOR DE ENTRADA (driver),
al mismo nivel que el router REST del CLI. NO contiene logica de negocio:
traduce tools MCP a paths REST y DELEGA en el Router (SSOT de dispatch).

```
  MCP client (agente)
       │ JSON-RPC stdio
       ▼
  [mcp_adapter]  ← adaptador de entrada (sin logica)
       │ traduce tools MCP → path REST
       ▼
  [Router]  ← SSOT de routing (mismo pipeline que el CLI)
       │
       ▼
  [handlers] → [use cases] → [domain]
```

MECANISMO DE COMPORTAMIENTO: al exponerse como MCP, las tools del walker
quedan disponibles EN EL CONTEXTO del agente (push), igual que el MCP de
memoria. El agente no puede olvidar llamar al walker.
"""
from __future__ import annotations

import json
import sys
from typing import Any


# ── Catalogo de tools (meta-data; el dispatch va por el Router) ────────────

_TOOLS = [
    {
        "name": "status",
        "description": "Estado del workflow del proyecto: node, sub_state, instrucciones, gates, progreso GPS",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "submit",
        "description": "Envia datos al sub-estado actual. AVANZA el workflow. Body: campos del nodo o intent para XOR",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "body": {"type": "object"},
            },
            "required": ["project_id", "body"],
        },
    },
    {
        "name": "transition",
        "description": "Transiciona por una gate (solo admin/emergencia; el agente avanza con submit)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "gate": {"type": "string"},
            },
            "required": ["project_id", "gate"],
        },
    },
    {
        "name": "kg_search",
        "description": "Busca en memorias del proyecto (cpg/ckg/delta/trace). CONSULTA antes de tocar codigo",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "query": {"type": "object"},
            },
            "required": ["project_id", "query"],
        },
    },
    {
        "name": "kg_multi",
        "description": "Consulta federada en multiples indices a la vez",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "layers": {"type": "object"},
            },
            "required": ["project_id", "layers"],
        },
    },
    {
        "name": "cpg_business",
        "description": "Capa de negocio del CPG: funciones puras reutilizables, flujos, patrones",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "file": {"type": "string"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "help",
        "description": "Protocolo y tareas disponibles del walker",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

_RESOURCES = [
    {
        "uri": "walker://status/{project_id}",
        "name": "Sprint Status",
        "description": "Snapshot del workflow: donde estas, instrucciones, gates, progreso GPS",
        "mimeType": "application/json",
    },
    {
        "uri": "walker://memory/{project_id}",
        "name": "Project Memory",
        "description": "CKG + business layer: decisiones, convenciones, funciones reutilizables",
        "mimeType": "application/json",
    },
]

_PROMPTS = [
    {
        "name": "workflow-protocol",
        "description": "Protocolo REST del walker: como avanzar, que consultar, cuando submit vs transition",
    },
]

_PROTOCOL_TEXT = (
    "PROTOCOLO WALKER (MCP):\n"
    "- Para AVANZAR en el workflow usa submit con los datos del nodo.\n"
    "- Si el nodo es una decision (XOR), usa submit con 'intent'.\n"
    "- Antes de tocar codigo, consulta memorias (kg_search/kg_multi).\n"
    "- Usa cpg_business para reutilizar funciones existentes.\n"
    "- Las decisiones se registran en CKG automaticamente (capture_decision)."
)


# ── Traduccion tool MCP → path REST (SSOT: el Router despacha) ─────────────

# Mapeo: tool_name -> (path_rest, builder_flags)
# El Router resuelve el path y ejecuta el handler con el pipeline completo.
_TOOL_ROUTES = {
    "status": ("{project_id}/status", lambda a: {}),
    "submit": ("{project_id}/submit", lambda a: {"_payload": a.get("body", {})}),
    "transition": ("{project_id}/transition", lambda a: {"gate": a.get("gate", "")}),
    "kg_search": ("{project_id}/kg_search", lambda a: {"_payload": a.get("query", {})}),
    "kg_multi": ("{project_id}/kg_multi", lambda a: {"_payload": a.get("layers", {})}),
    "cpg_business": ("{project_id}/cpg_business", lambda a: {"_payload": {"file": a.get("file", "")}}),
    "help": ("help", lambda a: {}),
}


def _call_tool(name: str, args: dict) -> dict:
    """Traduce una tool MCP a una ruta REST y delega en el Router (SSOT)."""
    route_tpl, flags_builder = _TOOL_ROUTES[name]
    project_id = args.get("project_id", "")
    path = route_tpl.format(project_id=project_id) if "{project_id}" in route_tpl else route_tpl
    flags = flags_builder(args)

    from . import _ROUTER

    result = _ROUTER.dispatch(path, flags, method="POST" if path.endswith(("submit", "transition")) else "GET")
    # El Router devuelve Response REST (SSOT). Serializar.
    if hasattr(result, "to_dict"):
        return result.to_dict()
    if isinstance(result, dict):
        return result
    return {"type": "ok", "data": {"result": result}}


def _read_resource(uri: str) -> dict:
    """Lee un resource del walker (contexto inyectable al agente)."""
    if uri.startswith("walker://status/"):
        project_id = uri.split("/")[-1]
        result = _call_tool("status", {"project_id": project_id})
        return {"contents": [{"uri": uri, "mimeType": "application/json",
                              "text": json.dumps(result, ensure_ascii=False)}]}
    return {"contents": [{"uri": uri, "mimeType": "application/json",
                          "text": json.dumps({"error": "resource no encontrado"})}]}


# ── JSON-RPC 2.0 handler ────────────────────────────────────────────────────


def _handle_request(msg: dict) -> dict:
    method = msg.get("method", "")
    params = msg.get("params", {}) or {}
    msg_id = msg.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False},
                    "prompts": {},
                },
                "serverInfo": {"name": "walker", "version": "3.1"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": _TOOLS}}
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments", {}) or {}
        try:
            result = _call_tool(name, args)
            return {
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "content": [{"type": "text",
                                 "text": json.dumps(result, ensure_ascii=False)}],
                    "isError": result.get("status_code", 200) >= 400,
                },
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0", "id": msg_id,
                "result": {"content": [{"type": "text", "text": str(e)}], "isError": True},
            }
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"resources": _RESOURCES}}
    if method == "resources/read":
        uri = params.get("uri", "")
        try:
            return {"jsonrpc": "2.0", "id": msg_id, "result": _read_resource(uri)}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": msg_id,
                    "result": {"contents": [{"uri": uri, "mimeType": "text/plain", "text": str(e)}]}}
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"prompts": _PROMPTS}}
    if method == "prompts/get":
        name = params.get("name", "")
        return {"jsonrpc": "2.0", "id": msg_id,
                "result": {"description": "Protocolo del workflow",
                           "messages": [{"role": "user", "content": {"type": "text", "text": _PROTOCOL_TEXT}}]}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": -32601, "message": f"Metodo '{method}' no soportado"}}


def serve_stdio() -> int:
    """Loop stdio JSON-RPC: lee requests linea a linea, responde."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            resp = _handle_request(msg)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": "Parse error"}}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()
    return 0


def main() -> int:
    """Entry point del adaptador MCP (driver). Sin logica de negocio."""
    return serve_stdio()


if __name__ == "__main__":
    sys.exit(main())
