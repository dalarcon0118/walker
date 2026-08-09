# language: es
Característica: Integración MCP de kg_search (regresión)
  Como desarrollador del sistema
  Quiero que el tool MCP kg_search despache end-to-end sin NameError
  Para que la búsqueda en la memoria del proyecto funcione vía el protocolo real

  Antecedentes:
    Dado que el servidor MCP compuesto está registrado

  Escenario: kg_search responde por la ruta real del protocolo MCP
    Dado que consulto las herramientas desde el protocolo MCP
    Cuando se hace tools/call a kg_search con query
    Entonces la respuesta tiene id y sin error JSON-RPC
    Y el content[0].text parseado tiene status_code 200
    Y el resultado contiene el índice ckg de la memoria del proyecto

  Escenario: kg_search con query string no lanza NameError por _payload
    Dado que el argumento query llega como string (flags_builder de la ruta)
    Cuando se hace tools/call a kg_search con query string
    Entonces la respuesta tiene id y sin error JSON-RPC
    Y el content[0].text parseado tiene status_code 200