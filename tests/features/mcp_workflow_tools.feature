# language: es
# tests/features/mcp_workflow_tools.feature
#
# Feature: MCP Workflow Tools — flujo completo JSON-RPC
#
# Como cliente MCP
# Quiero invocar submit, transition y capture_task via JSON-RPC
# Para operar el workflow completo sin salir del protocolo MCP

Característica: MCP Workflow Tools — flujo completo JSON-RPC
  Como cliente MCP
  Quiero invocar submit, transition y capture_task via JSON-RPC
  Para operar el workflow completo sin salir del protocolo MCP

  Antecedente: proyecto MCP con .cognitive/
    Dado un proyecto MCP con .cognitive/

  Escenario: submit envía datos al sub-estado actual
    Cuando invoco la tool MCP submit con decision test y evidence ok
    Entonces la respuesta MCP es exitosa
    Y el resultado tiene type ok

  Escenario: transition avanza por una gate
    Cuando invoco la tool MCP transition con gate start_task
    Entonces la respuesta MCP es exitosa
    Y el resultado tiene type ok

  Escenario: capture_task registra trabajo informal
    Cuando invoco la tool MCP capture_task con objective Trabajo informal y files test.py
    Entonces la respuesta MCP es exitosa
    Y el resultado tiene type ok

  Escenario: submit sin campos requeridos falla
    Cuando invoco la tool MCP submit vacía
    Entonces la respuesta MCP indica error

  Escenario: transition con gate inexistente falla
    Cuando invoco la tool MCP transition con gate no_existe
    Entonces la respuesta MCP indica error
