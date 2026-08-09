# language: es
# tests/features/f5_schema_coherence.feature
#
# Feature: Coherencia de contrato SSOT (F5)
#
# Como motor del workflow
# Quiero que los schemas de tools, help y DTOs sean coherentes
# Para que no haya alucinaciones ni contratos divergentes

Característica: Coherencia de contrato SSOT (F5 SPEC-043)
  Como motor del workflow
  Quiero que tools/list, walker://help, validate_llm_task_output y los DTOs sean coherentes
  Para que el contrato sea único y sin alucinaciones

  @catalogo
  Escenario: tools/list expone inputSchema y outputSchema por tool
    Dado el catálogo de tools del sistema
    Cuando se listan las tools
    Entonces cada tool tiene inputSchema dict object
    Y cada tool tiene outputSchema dict object
    Y cada tool tiene description

  @catalogo
  Escenario: Las tools de fase tienen outputSchema con properties válido
    Dado las 4 tools de fase del SPEC-043
    Cuando se consulta el catálogo
    Entonces kg_task_context, kg_diagnose, kg_qa_evidence y kg_sprint_evidence tienen outputSchema type object
    Y sus properties son dicts

  @catalogo
  Escenario: help_content se deriva de schemas, no de strings serializados
    Dado el catálogo de tools
    Cuando se pide ayuda de kg_task_context
    Entonces la ayuda tiene name, input_schema, output_schema y description
    Y los schemas son dicts, no strings serializados

  @contrato
  Escenario: validate_llm_task_output pasa para salida conforme
    Dado un nodo SPRINT.TASK_SPEC
    Cuando se valida una salida con task_id y status
    Entonces la validación pasa sin error
    Y el output se devuelve intacto

  @contrato
  Escenario: validate_llm_task_output rechaza salida fuera de contrato
    Dado un nodo SPRINT.TASK_SPEC
    Cuando se valida una salida sin task_id
    Entonces se eleva ContractOutputError (anti-alucinación)

  @contrato
  Escenario: Sin DTO declarado, validate_llm_task_output es fail-open
    Dado un nodo VIRTUAL.CUSTOM sin DTO
    Cuando se valida una salida arbitraria
    Entonces la validación pasa sin bloquear

  @workflow
  Escenario: CONTEXT_SCHEMAS tiene 9 providers con type object
    Dado el registro de CONTEXT_SCHEMAS
    Cuando se inspecciona cada schema
    Entonces hay 9 schemas
    Y cada schema tiene type object
    Y cada properties es dict

  @workflow
  Escenario: Los 16 sub_states llm_task están en WORKFLOW_OUTPUTS
    Dado el registro WORKFLOW_OUTPUTS
    Cuando se consulta
    Entonces hay al menos 16 entradas
    Y SPRINT.TASK_SPEC está registrado
    Y REFINE.DIAGNOSE está registrado

  @workflow
  Escenario: El output_schema del catálogo coincide con el DTO del workflow
    Dado el tool kg_diagnose en el catálogo
    Cuando se consulta su outputSchema
    Entonces es type object
    Y sus properties incluyen diagnosis y ok

  @envelope
  Escenario: Las tools de workflow declaran output_schema con envelope Response
    Dado las tools status, submit y transition
    Cuando se consultan sus outputSchema
    Entonces cada una es type object
    Y sus properties incluyen type, status_code, message y data
    Y data es objeto con campos propios de la tool

  @envelope
  Escenario: call_tool status devuelve siempre el envelope Response
    Dado un servidor MCP
    Cuando se llama a status con project_id walker y proyecto inexistente
    Entonces ambas respuestas tienen type, status_code, message y data
    Y status_code es int
    Y message es str
    Y data es dict

  @envelope
  Escenario: tools/call status devuelve envelope en content[0].text
    Dado un servidor MCP
    Cuando se hace tools/call a status
    Entonces la respuesta tiene id 1 y sin error
    Y el content[0].text parseado tiene type, status_code, message y data
