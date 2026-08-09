# language: es
# tests/features/protocol_domain.feature
#
# Feature: Dominio del Protocolo MCP (F1) — VOs, Entities y Aggregate.
#
# El bounded context "Protocol" define VOs con validación estricta,
# entidades con ciclo de vida y eventos, y un aggregate ToolRegistry
# como SSOT de tools/resources con invarianza de unicidad.

Característica: Dominio del Protocolo MCP
  Como arquitecto del bounded context "Protocol"
  Quiero VOs validados, entidades con ciclo de vida y un registry como SSOT
  Para garantizar consistencia en tools, sessions y recursos MCP

  @contrato
  Escenario: ToolName acepta nombres válidos
    Dado un validador de nombres de tool
    Cuando creo un ToolName con un nombre válido
    Entonces el ToolName es válido
    Y su valor es kg_search

  @contrato
  Escenario: ToolName rechaza nombres con caracteres inválidos
    Dado un validador de nombres de tool
    Cuando creo un ToolName con un nombre inválido
    Entonces el ToolName es rechazado
    Y el error indica Invalid tool name

  @contrato
  Escenario: ToolName rechaza nombres vacíos
    Dado un validador de nombres de tool
    Cuando creo un ToolName con cadena vacía
    Entonces el ToolName es rechazado
    Y el error indica cannot be empty

  @contrato
  Escenario: ProtocolVersion acepta versiones válidas
    Dado un validador de versiones de protocolo
    Cuando creo una ProtocolVersion con una versión válida
    Entonces la ProtocolVersion es válida

  @contrato
  Escenario: ProtocolVersion acepta versiones modernas de OpenCode
    Dado un validador de versiones de protocolo
    Cuando creo una ProtocolVersion con una versión moderna
    Entonces la ProtocolVersion es válida
    Y su valor es 2025-11-25

  @contrato
  Escenario: ProtocolVersion rechaza versiones antiguas
    Dado un validador de versiones de protocolo
    Cuando creo una ProtocolVersion con una versión antigua
    Entonces la ProtocolVersion es rechazada

  @contrato
  Escenario: UriPattern acepta URIs de walker
    Dado un validador de patrones URI
    Cuando creo un UriPattern con una URI de walker
    Entonces el UriPattern es válido

  @contrato
  Escenario: UriPattern rechaza URIs HTTP
    Dado un validador de patrones URI
    Cuando creo un UriPattern con una URI HTTP
    Entonces el UriPattern es rechazado

  @contrato
  Escenario: McpSession se crea sin inicializar
    Dado una factory de sesiones MCP
    Cuando creo una McpSession
    Entonces la sesión está en estado UNINITIALIZED

  @contrato
  Escenario: McpSession se inicializa y emite evento
    Dado una McpSession sin inicializar
    Cuando la inicializo con un cliente
    Entonces la sesión está en estado NEGOTIATED
    Y se emite un evento SessionInitialized

  @contrato
  Escenario: McpSession requiere inicialización previa
    Dado una McpSession sin inicializar
    Cuando llamo a require_initialized
    Entonces la guarda falla con errores

  @contrato
  Escenario: Tool requiere handler para crearse
    Dado una factory de tools
    Cuando creo una Tool sin handler
    Entonces la Tool es rechazada

  @contrato
  Escenario: Tool convierte a definición MCP
    Dado una Tool con handler
    Cuando llamo a to_definition
    Entonces la definición incluye el nombre
    Y la definición incluye inputSchema

  @contrato
  Escenario: ToolRegistry registra y lista tools
    Dado un ToolRegistry vacío
    Cuando registro una tool
    Entonces la tool aparece en list_tools
    Y su inputSchema es object-type

  @contrato
  Escenario: ToolRegistry bloquea duplicados
    Dado un ToolRegistry con status registrada
    Cuando intento registrar status nuevamente
    Entonces el registro falla
    Y el error indica already registered

  @contrato
  Escenario: ToolRegistry despacha tools desconocidas con error
    Dado un ToolRegistry vacío
    Cuando despacho una tool desconocida
    Entonces el despacho falla con Unknown tool

  @contrato
  Escenario: ToolRegistry despacha tools registradas correctamente
    Dado un ToolRegistry con ping registrada
    Cuando despacho ping
    Entonces el despacho retorna un resultado exitoso

  @contrato
  Escenario: ToolRegistry registra resources
    Dado un ToolRegistry vacío
    Cuando registro un resource con URI de walker
    Entonces el resource aparece en list_resources
    Y su URI es walker-schema

  @contrato
  Escenario: ToolRegistry recolecta eventos domain
    Dado un ToolRegistry vacío
    Cuando registro una tool
    Entonces se emite un evento ToolRegistered
    Y al recolectar nuevamente no hay eventos pendientes
