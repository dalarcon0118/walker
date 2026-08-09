Feature: Prompts gates
  Como orquestador de prompts
  Quiero que el sistema de prompts para USER GATES cargue templates reales
  Y resuelva placeholders y evidencias correctamente

  Scenario: PromptManager compose carga y renderiza el template real
    Given un proyecto con template "testnode/review"
    When compongo el prompt con datos extra
    Then el template renderizado contiene "QUÉ SUCEDIÓ"
    And contiene "Análisis completado"
    And contiene "approve"

  Scenario: resolve_prompt inyecta extra_data en el baggage
    Given un OrchestrationService
    And un proyecto con template "testnode/review"
    When resuelvo el prompt con extra_data de Q&A
    Then "¿Qué componentes?" aparece en el resultado
    And "A, B, C" aparece en el resultado

  Scenario: QueryManager expone last_fields desde sub_state_data
    Given un WorkflowState con Q&A del ANALYZE
    When guardo y cargo el estado
    And consulto last_fields
    Then last_fields.q1 es "¿Qué?"
    And el nodo es "ANALYZE.REVIEW"

  Scenario: El motor de evidencia genera reporte con EVIDENCIA y META-INSTRUCTION
    Given un detalle de verificación con método shell
    When genero el reporte de evidencia
    Then el reporte contiene "EVIDENCIA"
    And contiene "META-INSTRUCTION"
    And contiene "NO CUMPLE"

  Scenario: Gate briefing renderiza 3 secciones
    Given un what_happened sintetizado
    And gates de negocio
    When renderizo el gate briefing
    Then contiene "QUÉ SUCEDIÓ"
    And contiene "POR QUÉ / EVIDENCIA"
    And contiene "TU SIGUIENTE PASO"
    And contiene "analysis_approved"

  Scenario: what_happened maneja decisión, intent y data
    Given estado con decisión approve
    When what_happened sintetiza el estado
    Then what_happened contiene "Decisión registrada: approve"
    Given estado con intent start_task
    When what_happened sintetiza el estado
    Then what_happened contiene "Intención del paso: start_task"
    Given estado con task_data
    When what_happened sintetiza el estado
    Then what_happened contiene "task_data:"
    Given estado vacío
    When what_happened sintetiza el estado
    Then what_happened contiene "no hay resultado del paso anterior"

  Scenario: PromptContextBuilder expone what_happened desde Q&A del ANALYZE
    Given un chain_dir de proyecto
    When construyo el contexto del prompt
    Then el contexto contiene "what_happened" o "task_table"

  Scenario: Health analyzer sugiere submit en user gate
    Given un health report con execution_type=user_input
    When analizo la salud
    Then el paso priority 4 sugiere "submit"
    And no sugiere "transition <gate_name>"

  Scenario: Health analyzer mantiene transition en gates de nodo
    Given un health report con execution_type=llm_task
    When analizo la salud
    Then el paso priority 4 sugiere "transition <gate_name>"
    And no sugiere "submit"
