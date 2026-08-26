Feature: Protocol contracts
  Como arquitecto de contratos
  Quiero que los contratos del protocolo MCP validen correctamente
  Y que las respuestas y errores se serialicen bien

  Scenario: ToolDefinition crea un DTO válido
    Given un ToolDefinition con nombre "recall_knowledge"
    When creo el DTO
    Then el DTO es válido
    And el nombre es "recall_knowledge"

  Scenario: ToolDefinition rechaza nombre vacío
    Given un ToolDefinition con nombre vacío
    When creo el DTO
    Then el DTO es None
    And el error contiene "cannot be empty"

  Scenario: ToolDefinition serializa a dict correctamente
    Given un ToolDefinition válido
    When lo convierto a dict
    Then name es "status"
    And inputSchema es {"type": "object"}

  Scenario: JsonRpcRequest crea request válida
    Given un JsonRpcRequest con método "tools/list"
    When creo la request
    Then el método es "tools/list"

  Scenario: JsonRpcRequest rechaza método inválido
    Given un JsonRpcRequest con método "bogus/method"
    When creo la request
    Then la request es None
    And el error contiene "Unsupported method"

  Scenario: JsonRpcRequest detecta notificación
    Given un JsonRpcRequest con método "notifications/initialized"
    When creo la request
    Then es notificación

  Scenario: InitializeRequest parsea desde params
    Given params con protocolVersion y clientInfo
    When creo InitializeRequest
    Then protocol_version es "2024-11-05"
    And client_name es "opencode"

  Scenario: ToolCallRequest parsea desde params
    Given params con name="status" y arguments={"x": 1}
    When creo ToolCallRequest
    Then name es "status"
    And arguments es {"x": 1}

  Scenario: JsonRpcResult serializa a dict
    Given un JsonRpcResult con id=1 y result={"tools": []}
    When lo convierto a dict
    Then jsonrpc es "2.0"
    And id es 1

  Scenario: JsonRpcError serializa a dict
    Given un JsonRpcError con code=-32601
    When lo convierto a dict
    Then error.code es -32601

  Scenario: Response.ok crea respuesta exitosa
    Given un Response de tipo "status"
    When creo la respuesta ok
    Then status_code es 200
    And type es "status"

  Scenario: Response.error crea respuesta de error
    Given un Response con mensaje "Not found"
    When creo la respuesta error con 404
    Then status_code es 404
    And message es "Not found"

  Scenario: ErrorResponse valida errores estándar
    Given un ErrorResponse con code=-32601
    When creo el error
    Then es válido
    Given un ErrorResponse con code=0 y mensaje vacío
    When creo el error
    Then es None y tiene errores

  Scenario: NextAction serializa a dict
    Given una NextAction con run="walker submit"
    When la convierto a dict
    Then run es "walker submit"
    And description es "Advance"
