Feature: Prompts SSOT
  Como arquitecto de prompts
  Quiero que los templates vivan en un root único features/prompts/templates/
  Y que el código se sirva desde esos templates sin duplicar contenido

  Scenario: Los templates de protocolo existen en el root único
    Given el directorio de templates canónico
    Then protocol/mcp.md existe
    And protocol/rest.md existe
    And mcp.md contiene "MANDATORY BEHAVIOR CONTRACT" y "capture_task"
    And rest.md contiene "PROTOCOLO REST OBLIGATORIO", "submit" y "transition"

  Scenario: Gate briefing vive en subpath gates/
    Given el directorio de templates canónico
    Then gates/gate-briefing.md existe
    And contiene "QUÉ SUCEDIÓ"

  Scenario: _PROTOCOL_TEXT es exactamente el template MCP
    Given el template protocol/mcp.md
    When comparo con _PROTOCOL_TEXT del mcp_adapter
    Then son idénticos

  Scenario: El protocol REST de _build_help es exactamente el template
    Given el template protocol/rest.md
    When invoco _build_help
    Then el campo "protocol" coincide con el template

  Scenario: Fallbacks activos si el template falta
    Given el loader patcheado para lanzar TemplateNotFound
    When cargo el protocolo MCP
    Then devuelve _PROTOCOL_FALLBACK
    When cargo el protocolo REST
    Then devuelve _PROTOCOL_REST_FALLBACK

  Scenario: TemplateLoader resuelve el root canónico primero
    Given un TemplateLoader con chain temporal
    When cargo "protocol/mcp"
    Then contiene "WALKER PROTOCOL (MCP)" y "capture_task"
    When cargo "gates/gate-briefing"
    Then contiene "QUÉ SUCEDIÓ"

  Scenario: Los prompts de nodo viven en templates/nodes/
    Given el directorio templates/nodes
    Then existen 47 archivos .prompt.json
    And features/engine/workflow no tiene carpeta prompts
    And .cognitive no tiene carpeta workflow.v3

  Scenario: TemplateLoader resuelve prompts de nodo
    Given un TemplateLoader con chain temporal
    When cargo "sprint/task-review"
    Then contiene "REVISION DE ESPECIFICACION DE TAREA"
    When cargo "implement/work"
    Then contiene "{step_quality_summary}"
    When cargo "refine/diagnose"
    Then contiene "{step_recommend}"

  Scenario: available_templates incluye prompts de nodo
    Given un PromptManager con chain temporal
    When listo templates disponibles
    Then incluye "nodes/sprint/task-review.prompt.json"
    And incluye "nodes/implement/work.prompt.json"
    And hay exactamente 50 templates

  Scenario: Engine workflow es código, no data
    Given el path resolver
    When obtengo el engine workflow dir
    Then termina en features/engine/workflow
    And no contiene ".cognitive"
    And existe nodes/implement.node.json

  Scenario: available_templates lista subpaths del root canónico
    Given un PromptManager con chain temporal
    When listo templates disponibles
    Then incluye "protocol/mcp.md"
    And incluye "protocol/rest.md"
    And incluye "gates/gate-briefing.md"
