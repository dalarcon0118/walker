# language: es
# tests/features/workflow_contracts.feature
#
# Feature: DTOs de respuesta de los 16 sub_states llm_task (F3 SPEC-043).
#
# RF-B2 (ADR-043 §5.2-B): cada sub_state llm_task tiene un DTO de respuesta
# con from_dict() que valida contra el output_schema del node.json.

Característica: DTOs de respuesta de los sub_states llm_task
  Como motor de workflow
  Quiero DTOs tipados para cada sub_state llm_task con validación estricta
  Para garantizar que la salida del LLM cumple el contrato del workflow

  @contrato
  Escenario: Los 16 sub_states llm_task tienen DTO registrado
    Dado el registro WORKFLOW_OUTPUTS
    Cuando verifico cada uno de los 16 sub_states llm_task
    Entonces cada par (node, sub_state) tiene un DTO de respuesta

  @contrato
  Escenario: from_dict acepta salida válida
    Dado los DTOs TaskSpecOutput, DiagnoseOutput y CodeReviewOutput
    Cuando creo instancias con datos válidos
    Entonces los DTOs se construyen sin error
    Y los campos tienen los valores esperados

  @contrato
  Escenario: from_dict rechaza salida fuera de contrato
    Dado los DTOs TaskSpecOutput, DiagnoseOutput y CodeReviewOutput
    Cuando creo instancias con datos inválidos
    Entonces se eleva ContractOutputError por campo faltante
    Y se eleva ContractOutputError por tipo incorrecto
    Y se eleva ContractOutputError por valor vacío

  @contrato
  Escenario: from_dict rechaza valores de enum fuera de rango
    Dado los DTOs SelectOutput y AddTaskOutput
    Cuando creo instancias con decisión/status inválidos
    Entonces se eleva ContractOutputError para valores fuera de enum

  @contrato
  Escenario: to_schema es coherente con el node.json
    Dado el DTO TaskSpecOutput
    Cuando genero el schema con to_schema
    Entonces el schema tiene type object
    Y required incluye task_id y status
    Y properties incluye task_id

  @contrato
  Escenario: validate_llm_task_output es fail-open sin DTO
    Dado el validador de salida llm_task
    Cuando valido un sub-state sin DTO registrado
    Entonces retorna la salida sin bloquear

  @contrato
  Escenario: validate_llm_task_output valida con DTO presente
    Dado el validador de salida llm_task
    Cuando valido SPRINT.TASK_SPEC sin task_id
    Entonces eleva un error de contrato

  @contrato
  Escenario: SubState.is_llm_task clasifica correctamente
    Dado entidades SubState con diferentes ejecuciones
    Cuando consulto is_llm_task
    Entonces llm_task retorna True
    Y manual retorna False
    Y auto retorna False
