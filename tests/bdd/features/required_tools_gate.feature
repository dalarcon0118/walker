# language: es
# tests/features/required_tools_gate.feature
#
# Feature: Gate de required_tools en submit (CKG doc-28).
#
# Un sub-estado con required_tools declara capas que el agente DEBE consultar
# antes de submit; el engine BLOQUEA el submit si el TraceGraph no registra
# evidencia de consulta. Fail-open si el tracer falla.

Característica: Gate de required_tools en submit
  Como motor de workflow
  Quiero que el submit verifique que el agente consultó las capas requeridas
  Para garantizar conocimiento previo antes de avanzar

  @contrato
  Escenario: Sub-estados llm_task declaran required_tools
    Dado el nodo IMPLEMENT del workflow
    Cuando examino los sub-estados WORK, CODE_REVIEW_SCAN y KNOWLEDGE_UPDATE
    Entonces cada uno declara required_tools
    Y cada lista incluye ckg

  @contrato
  Escenario: Sub-estados manuales no declaran required_tools
    Dado el nodo IMPLEMENT del workflow
    Cuando examino los sub-estados USER_GATE, CODE_REVIEW_RESULT y REPORT_GATE
    Entonces ninguno declara required_tools

  @contrato
  Escenario: WorkflowRepository parsea required_tools desde JSON
    Dado el workflow cargado desde el chain_dir
    Cuando obtengo el SubState WORK de IMPLEMENT
    Entonces required_tools está parseado como lista
    Y el primer elemento tiene id ckg y command_hint

  @contrato
  Escenario: Submit bloquea sin kg_query registrada
    Dado un SubmitOrchestrator con tracer sin eventos
    Y un contexto en IMPLEMENT.WORK con required_tools
    Cuando ejecuto submit
    Entonces la acción es gate_blocked
    Y el error_type es required_tools_gate
    Y el mensaje menciona las capas requeridas
    Y submit_uc no es llamado

  @contrato
  Escenario: Submit avanza con kg_query task_id coincidente
    Dado un SubmitOrchestrator con tracer con kg_query de TASK-001
    Y un contexto en IMPLEMENT.WORK con required_tools
    Cuando ejecuto submit
    Entonces la acción es advanced
    Y submit_uc es llamado una vez

  @contrato
  Escenario: Submit avanza con consulta CLI de capa requerida
    Dado un SubmitOrchestrator con tracer con kg_query session cli índice cpg
    Y un contexto en IMPLEMENT.WORK con required_tools
    Cuando ejecuto submit
    Entonces la acción es advanced
    Y submit_uc es llamado una vez

  @contrato
  Escenario: Submit avanza con search_memories que cubre capas
    Dado un SubmitOrchestrator con tracer con kg_query index multi
    Y un contexto en IMPLEMENT.WORK con required_tools
    Cuando ejecuto submit
    Entonces la acción es advanced
    Y submit_uc es llamado una vez

  @contrato
  Escenario: Gate no aplica a sub-estados sin required_tools
    Dado el SubState USER_GATE sin required_tools
    Cuando ejecuto _check_required_tools_gate
    Entonces el gate retorna ok

  @contrato
  Escenario: Gate es fail-open si el tracer falla
    Dado un SubmitOrchestrator con tracer que lanza excepción
    Y un contexto en IMPLEMENT.WORK con required_tools
    Cuando ejecuto _check_required_tools_gate
    Entonces el gate retorna ok con advertencia

  @contrato
  Escenario: Briefing renderiza sección REQUIRED TOOLS
    Dado el SubState WORK de IMPLEMENT con required_tools
    Cuando genero el node briefing
    Entonces el texto incluye REQUIRED TOOLS
    Y el texto incluye ckg y cpg
    Y el texto incluye advertencia de bloqueo

  @contrato
  Escenario: Replay devuelve trace completo sin truncar
    Dado un proyecto con 80 eventos kg_query en el trace
    Cuando el TraceLogger hace replay
    Entonces se recuperan los 80 eventos
    Y has_kg_query detecta la consulta
