# language: es
# tests/features/mcp_adapter.feature
#
# Feature: MCP Adapter — JSON-RPC 2.0 y tools/call
#
# Como cliente MCP
# Quiero que el adapter JSON-RPC 2.0 responda correctamente
# Para poder invocar tools y recursos del walker

Característica: MCP Adapter — JSON-RPC 2.0 y tools/call
  Como cliente MCP
  Quiero que el adapter JSON-RPC 2.0 responda correctamente
  Para poder invocar tools y recursos del walker

  Escenario: initialize devuelve capabilities y sessionId
    Dado que invoco _handle_request con initialize
    Entonces la respuesta tiene jsonrpc 2.0
    Y la respuesta tiene id
    Y capabilities existe
    Y serverInfo es walker

  Escenario: tools/list devuelve el catálogo
    Dado que invoco _handle_request con tools/list
    Entonces la respuesta tiene result.tools
    Y result.tools es una lista no vacía

  Escenario: tools/call a status funciona
    Dado que invoco _handle_request con tools/call status
    Entonces tipo es text
    Y texto es JSON parseable

  Escenario: tools/call a tool inexistente
    Dado que invoco _handle_request con tools/call no_existe
    Entonces la respuesta indica error

  Escenario: ping devuelve vacío
    Dado que invoco _handle_request con ping
    Entonces la respuesta tiene result vacío

  Escenario: método no soportado devuelve error
    Dado que invoco _handle_request con no_soportado
    Entonces la respuesta tiene error
