# language: es
Característica: Integración MCP de recall_knowledge (regresión)
  Como desarrollador del sistema
  Quiero que el tool MCP recall_knowledge despache end-to-end sin NameError
  Para que la búsqueda en la memoria del proyecto funcione vía el protocolo real

  Antecedentes:
    Dado que el servidor MCP compuesto está registrado

  Escenario: recall_knowledge responde por la ruta real del protocolo MCP
    Dado que consulto las herramientas desde el protocolo MCP
    Cuando se hace tools/call a recall_knowledge con query
    Entonces la respuesta tiene id y sin error JSON-RPC
    Y el content[0].text parseado tiene status_code 200
    Y el resultado contiene el índice ckg de la memoria del proyecto

  Escenario: recall_knowledge con query string no lanza NameError por _payload
    Dado que el argumento query llega como string (flags_builder de la ruta)
    Cuando se hace tools/call a recall_knowledge con query string
    Entonces la respuesta tiene id y sin error JSON-RPC
    Y el content[0].text parseado tiene status_code 200