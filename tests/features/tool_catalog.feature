# language: es
# tests/features/tool_catalog.feature
#
# Feature: Catálogo público de tools con outputSchema (ADR-043 SSOT).
#
# El catálogo que alimenta `tools/list` proyecta outputSchema para que el
# agente vea el contrato de salida de las tools (punto 4 del flujo:
# ModuleRegistry → tool_catalog → tools/list).

Característica: Catálogo público de tools con outputSchema
  Como agente MCP
  Quiero ver el outputSchema de cada tool en tools/list
  Para saber qué contrato de salida devuelve cada herramienta

  @contrato
  Escenario: tools/list proyecta outputSchema cuando el DTO lo declara
    Dado un ModuleRegistry con una tool demo con outputSchema declarado
    Cuando listo las tools del catálogo
    Entonces la tool demo tiene outputSchema
    Y el outputSchema coincide con el declarado

  @contrato
  Escenario: tools/list emite outputSchema vacío si no hay declaración
    Dado un ModuleRegistry con una tool demo sin outputSchema
    Cuando listo las tools del catálogo
    Entonces la tool demo tiene outputSchema vacío

  @contrato
  Escenario: get_tool proyecta outputSchema
    Dado un ModuleRegistry con una tool demo con outputSchema declarado
    Cuando obtengo la tool por nombre
    Entonces la tool tiene outputSchema
    Y el outputSchema coincide con el declarado

  @contrato
  Escenario: Catálogo fusiona tools del ModuleRegistry con tasks públicas
    Dado el catálogo sin registry inyectado (bootstrap completo)
    Cuando listo las tools del catálogo
    Entonces las tasks de fase están presentes
    Y kg_task_context proyecta outputSchema
    Y kg_diagnose proyecta outputSchema con propiedad diagnosis
