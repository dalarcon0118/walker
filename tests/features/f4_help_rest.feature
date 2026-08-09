# language: es
# tests/features/f4_help_rest.feature
#
# Feature: Help REST sin redefinir contrato (F4)
#
# Como consumidor de herramientas
# Quiero que la ayuda y las rutas REST se derive del registro SSOT
# Para no tener contratos duplicados ni desincronizados

Característica: Help y REST derivados del registro SSOT (F4)
  Como consumidor de herramientas MCP/REST
  Quiero que walker://help y las rutas REST se generen desde la metadata del registro
  Para no redefinir contratos ni tener fuentes de verdad paralelas

  @help
  Escenario: build_tool_help deriva description, example y schemas del registro
    Dado un tool registrado con description, example y schemas
    Cuando se construye la ayuda del tool
    Entonces el documento de ayuda tiene el nombre correcto
    Y la description se deriva del registro
    Y el example se deriva del registro
    Y los schemas de input y output se derivan como dicts

  @help
  Escenario: build_tool_help retorna None para tool inexistente
    Dado un registro con una tool demo
    Cuando se pide ayuda de un tool que no existe
    Entonces el resultado es None (respuesta 404, no texto inventado)

  @help
  Escenario: _render_help_text refleja description, usage, example y schemas
    Dado un documento de ayuda con todos los campos
    Cuando se renderiza el texto de ayuda
    Entonces el texto contiene la description
    Y contiene el usage
    Y contiene el example
    Y contiene las propiedades de los schemas

  @resources
  Escenario: El server expone el patrón walker://help/{tool} en resources/list
    Dado un servidor MCP construido
    Cuando se listan los recursos
    Entonces existe el recurso walker://help/{tool}

  @resources
  Escenario: resources/read de tool pública devuelve su documentación
    Dado un servidor MCP con tools registradas
    Cuando se lee el recurso walker://help/kg_diagnose
    Entonces la respuesta tiene contents con uri walker://help/kg_diagnose
    Y el texto contiene # Tool: kg_diagnose
    Y el texto contiene Output schema

  @resources
  Escenario: resources/read de tool inexistente devuelve error
    Dado un servidor MCP con tools registradas
    Cuando se lee el recurso de un tool inexistente
    Entonces la respuesta contiene error con not found

  @rest
  Escenario: flags_builder traduce parámetros al payload declarado
    Dado una REST route con flags_builder
    Cuando se invoca con body status: submitted
    Entonces el flags result es el payload declarado sin campos nuevos

  @rest
  Escenario: Sin flags_builder, el payload pasa tal cual sin inventar campos
    Dado una REST route sin flags_builder
    Cuando se invoca con un body
    Entonces el flags result es el payload original sin modificación
