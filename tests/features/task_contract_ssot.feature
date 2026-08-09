# language: es
# tests/features/task_contract_ssot.feature
#
# Feature: SSOT del contrato de tareas en metadata del registry.
#
# task-manifests eliminado: el contrato vive en la metadata del task registry
# y el MCP lo expone como input_schema en discover() y help.

Característica: SSOT del contrato de tareas en el registry
  Como agente MCP
  Quiero ver el input_schema de cada tarea pública en discover() y help
  Para saber qué parámetros requiere cada tool antes de invocarla

  @contrato
  Escenario: discover() expone input_schema para tasks con contrato
    Dado el task registry bootstrapeado
    Cuando descubro todas las tasks
    Entonces kg_research expone input_schema
    Y kg_index_repo expone input_schema
    Y los schemas tienen type object
    Y los schemas tienen clave required

  @contrato
  Escenario: discover() no fabrica input_schema para tasks sin contrato
    Dado el task registry bootstrapeado
    Cuando descubro todas las tasks
    Entonces list_projects no tiene input_schema

  @contrato
  Escenario: help_task incluye schemas de las tasks públicas
    Dado el handler de help construido
    Cuando obtengo el catálogo de tareas
    Entonces kg_research aparece en el catálogo
    Y kg_research incluye input_schema
