Feature: McpServer registry
  Como integrador de herramientas MCP
  Quiero que el McpServer gestione herramientas sin duplicados ni nombres vacíos
  Y que el ToolRegistry y el McpServer mantengan el mismo schema (SSOT)

  Scenario: Bloquea registro duplicado de herramienta
    Given un McpServer vacío
    When registro la herramienta "status"
    And intento registrar otra herramienta con el mismo nombre "status"
    Then el segundo registro falla
    And el error contiene "already registered"

  Scenario: Bloquea registro con nombre vacío
    Given un McpServer vacío
    When intento registrar una herramienta con nombre vacío
    Then el registro falla

  Scenario: Registra herramientas desde el ToolRegistry manteniendo el schema
    Given un McpServer vacío
    Given un ToolRegistry con herramientas "status" y "submit"
    When registro todas las herramientas del ToolRegistry en el McpServer
    When alisto las herramientas del McpServer
    Then el McpServer reporta 2 herramientas registradas
    And al listar las herramientas aparecen "status" y "submit"

  Scenario: Despacha herramientas del ToolRegistry
    Given un McpServer vacío
    Given un ToolRegistry con herramientas "status" y "submit"
    When registro todas las herramientas del ToolRegistry en el McpServer
    When despacho la herramienta "status"
    Then al despachar "status" se obtiene el resultado correcto

  Scenario: El schema de herramienta coincide entre registry y server
    Given un McpServer vacío
    Given un ToolRegistry con la herramienta "ping" y un input_schema explícito
    When registro la herramienta en el McpServer
    Then el inputSchema del server coincide con el del registry
    And la descripción del server coincide con la del registry
