Feature: Phase intelligence tasks
  Como arquitecto de contratos de fase
  Quiero que las 4 tasks públicas de fase registren input_schema y output_schema
  Y que el output_schema derive del DTO de contrato (SSOT)

  Scenario: Registra 4 tools con contrato completo
    Given un registry vacío
    When registro las tasks de fase
    Then hay 4 tools registradas
    And cada una tiene input_schema y output_schema

  Scenario: output_schema deriva del DTO de cada fase
    Given un registry vacío
    When registro las tasks de fase
    Then kg_task_context output_schema es TaskFileIntelligenceDTO.to_schema()
    And kg_diagnose output_schema es FailureDiagnosisDTO.to_schema()
    And kg_qa_evidence output_schema es QaCoverageDTO.to_schema()
    And kg_sprint_evidence output_schema es SprintEvidenceDTO.to_schema()

  Scenario: output_schema incluye phase y campos clave
    Given un registry vacío
    When registro las tasks de fase
    Then kg_task_context output_schema tiene properties.phase.const="IMPLEMENT"
    And kg_task_context tiene property "file" y risk con enum LOW MED HIGH
    And kg_qa_evidence output_schema tiene properties.phase.const="VERIFY"

  Scenario: kg_task_context delega en el servicio y envuelve fallo
    Given un execution context con chain_dir inexistente
    When ejecuto kg_task_context
    Then el resultado tiene atributo ok

  Scenario: Bootstrap expone las 4 tasks públicas con output_schema
    Given el bootstrap de tasks
    When registro las tasks públicas
    Then cada una está en el registry con scope public
    And cada una tiene input_schema y output_schema en el registry
