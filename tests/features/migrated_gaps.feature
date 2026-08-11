Feature: Migrated gaps
  Como desarrollador del pipeline
  Quiero validar que los módulos migrados funcionan correctamente
  Para mantener la cobertura sin depender de legacy

  Scenario: La síntesis de tareas genera tipos correctos desde predicates
    Given los predicates de entrada con must_exist, must_not_exist e invariants
    And los archivos conocidos del proyecto
    When genero tareas desde los predicates
    Then obtengo 3 tipos: feature, bugfix y test
    And la feature tiene "auth" en el objetivo
    And la feature tiene archivos impactados
    And el bugfix tiene validación que empieza por "NO "
    And el test tiene validación que empieza por "invariant:"

  Scenario: La síntesis genera una tarea fallback sin predicates
    Given predicates vacíos
    When genero tareas desde predicates vacíos
    Then obtengo exactamente 1 tarea
    And la tarea tiene task_id "TASK-001"
    And la tarea es de tipo feature

  Scenario: fill_task_contexts completa campos de tarea
    Given tareas generadas desde predicates
    When completo los contextos de las tareas
    Then cada tarea tiene observation, task_description y acceptance_criteria

  Scenario: enrich_tasks asigna predicates, files y riesgo
    Given una tarea genérica desde el backlog
    And predicates con must_exist
    And archivos conocidos
    When enriquezco la tarea con predicates
    Then la tarea tiene predicates de validación
    And los archivos impactados son los conocidos

  Scenario: compute_total_risk escala con la cantidad de predicates
    Given pocos predicates
    When calculo el riesgo total
    Then el riesgo está entre 0 y 0.5
    And con más predicates el riesgo aumenta
    And el riesgo nunca supera 0.5

  Scenario: collect_known_files aplana y deduplica
    Given listas de archivos y tareas con archivos impactados
    When recolecto archivos conocidos
    Then obtengo la lista aplana sin duplicados

  Scenario: El backlog WSJF prioriza items por valor/esfuerzo
    Given un backlog con dos items
    When cargo el backlog
    Then el primer item es "Implementar login" con mayor ratio WSJF

  Scenario: La planning ceremony propone, refina y confirma sin generar plan.json
    Given un proyecto con chain y backlog
    When ejecuto propose, refine y commit
    Then el plan se propone con rc=3
    And el refine con edición de tareas devuelve rc=0
    And el commit devuelve rc=0
    And no existe plan.json (delta es la fuente única)
    And el spring existe en el delta
    And hay 3 specs en el delta
    And todos los items del backlog están in-sprint

  Scenario: verify_engine detecta símbolo presente y ausente
    Given un proyecto con un símbolo existente
    And un símbolo que no existe
    When verifico la existencia de símbolos
    Then el símbolo existente pasa
    And el símbolo ausente falla

  Scenario: verify_engine detecta símbolo NO existente
    Given un proyecto con un símbolo existente
    And un símbolo zombi
    When verifico que el símbolo no exista
    Then el zombi pasa la verificación
    And el símbolo existente falla

  Scenario: verify_engine detecta archivo existente y ausente
    Given un proyecto con un archivo existente
    And un archivo faltante
    When verifico la existencia de archivos
    Then el archivo existente pasa
    And el archivo faltante falla

  Scenario: verify_engine detecta salida de comando
    Given un proyecto en un directorio temporal
    When ejecuto un comando que contiene "42"
    Then la verificación pasa con método "command_output"
    And si busco "NOPE" falla

  Scenario: verify_engine siempre pasa para verificación manual
    Given un proyecto en un directorio temporal
    When ejecuto una verificación manual
    Then pasa con método "manual"

  Scenario: verify_engine cachea resultados iguales
    Given un proyecto con símbolo existente
    When ejecuto la misma verificación dos veces
    Then ambos resultados son idénticos

  Scenario: verify_engine batch mantiene orden y cache
    Given un proyecto con tres predicates
    When ejecuto el batch
    Then los resultados mantienen el orden [True, True, False]

  Scenario: verify_predicates_task envuelve resultados en TaskResult
    Given un proyecto con dos predicates
    When ejecuto verify_predicates como task
    Then la task pasa
    And all_passed es False
    And failed_count es 1
    And passed_count es 1

  Scenario: _parse_predicate detecta shell vs lenguaje natural
    Given una sentencia en lenguaje natural
    Given una sentencia de shell
    When parseo ambos predicates
    Then el lenguaje natural se clasifica como must_exist
    And la shell se clasifica como comando

  Scenario: run_verification ejecuta tests unitarios
    Given un archivo de test que pasa
    And un test inexistente
    When ejecuto run_verification tipo test
    Then el test existente pasa
    And el inexistente falla

  Scenario: El envelope estándar de side_effect_reporta status ok/failed/degraded
    Given un side effect de tipo update_cpg
    When creo envelopes ok, failed y degraded
    Then ok tiene status "ok" y signals correctos
    And failed tiene status "failed"
    And degraded tiene status "degraded"

  Scenario: Scope inválido se degrada a SCOPE_TRACKING
    Given un envelope con scope inexistente
    When creo el envelope ok
    Then el scope se degrada a SCOPE_TRACKING

  Scenario: normalize envuelve dicts legacy al envelope estándar
    Given un dict legacy con ok=True y files=5
    When normalizo al envelope estándar
    Then el envelope es estándar con status "ok"
    And metadata contiene files=5

  Scenario: normalize con None produce fallo
    Given None como resultado
    When normalizo al envelope estándar
    Then el envelope tiene status "failed"

  Scenario: dispatch detecta quality_gate_failed cuando quality_passed=False
    Given un OrchestrationService con repos falsos
    And un side effect con quality_passed=False
    When despacho eventos desde side effects
    Then se dispara 1 evento quality_gate_failed
    And el resultado registra failed_gates y recommend

  Scenario: dispatch no dispara eventos cuando quality_passed=True
    Given un OrchestrationService con repos falsos
    And un side effect con quality_passed=True
    When despacho eventos desde side effects
    Then no se disparan eventos

  Scenario: TaskResult map transforma y short-circuita en fallos
    Given un TaskResult exitoso
    When aplico map
    Then se transforma el dato
    Given un TaskResult fallido
    When aplico map
    Then se short-circuitea sin error

  Scenario: TaskResult and_then encadena operaciones
    Given un TaskResult exitoso con valor 2
    When encadeno and_then con *3 y +1
    Then obtengo 7
    Given un TaskResult exitoso con valor 1
    When encadeno un and_then que falla
    Then el siguiente and_then no se ejecuta

  Scenario: TaskResult tap y recover
    Given un TaskResult exitoso
    When aplico tap para observar
    Then el valor permanece
    Given un TaskResult fallido
    When aplico recover
    Then obtengo un valor de respaldo

  Scenario: StepResult agrega signals de calidad
    Given un side effect con quality_passed=False
    When creo un StepResult con ese side effect
    Then quality() reporta quality_passed=False
    And failed_gates incluye cyclomatic_complexity
    And to_prompt_dict expone step_failed_gates

  Scenario: _read_last_step_result lee desde sub_state_data
    Given sub_state_data con campos del paso previo
    When leo el último step result
    Then obtengo los campos correctos
    Given sub_state_data vacío
    When leo el último step result
    Then obtengo un dict vacío

  Scenario: Los templates de nodo tienen placeholders de paso
    Given el template implement/work
    And el template implement/code-review-scan
    And el template refine/diagnose
    Then implement/work tiene {step_quality_summary}, {step_failed_gates} y {step_recommend}
    And implement/code-review-scan tiene {step_quality_passed} y {step_failed_gates}
    And refine/diagnose tiene {step_quality_summary} y {step_recommend}

  Scenario: El renderer resuelve placeholders y no deja rotos
    Given datos de calidad fallida
    When renderizo cada template con datos
    Then cyclomatic_complexity aparece en el render
    When renderizo sin datos
    Then no quedan placeholders sin resolver

  Scenario: as_side_effect envuelve con scope knowledge
    Given una función decorada con scope knowledge
    When la ejecuto con contexto
    Then el envelope es estándar con scope SCOPE_KNOWLEDGE
    And campos legacy y signals son consistentes

  Scenario: as_side_effect con scope decision
    Given una función decorada con scope decision
    When la ejecuto con contexto
    Then el envelope es estándar con scope SCOPE_DECISION
    And signals contienen decision="approve"

  Scenario: emit_event genera envelope con scope event
    Given una función emit_event
    When la ejecuto con evento qa_failed
    Then el envelope tiene scope "event"
    And status es "ok"

  Scenario: dispatch de emit_event funciona con el envelope estándar
    Given un OrchestrationService con repos falsos
    And un side effect emit_event
    When despacho eventos desde side effects
    Then se dispara 1 evento

  Scenario: from_dict valida y retorna Result
    Given un VO TaskInput con files como lista
    When creo desde dict válido
    Then es exitoso con files correctos
    Given un VO TaskInput con files como string
    When creo desde dict inválido
    Then falla con el mensaje de validación
    Given un VO TaskInput con dict None
    When creo desde dict None
    Then es exitoso

  Scenario: from_dict encadena con monadas
    Given un VO Demo con n=2
    When aplico map multiplicando por 10
    Then obtengo 20
    Given un VO Demo con n="no-int"
    When aplico map
    Then no crashea

  Scenario: VOs de tasks validan correctamente
    Given ReviewInput con files y thresholds
    Then es exitoso
    Given ReviewInput con files como string
    Then falla
    Given VerifyInput con predicates normalizados
    Then normalized_predicates devuelve la lista
    Given VerifyInput con predicate único
    Then normalized_predicates devuelve el string
    Given VerifyInput vacío
    Then falla
    Given ImplementInput con objective y files
    Then es exitoso
    Given ImplementInput con files como string
    Then falla

  Scenario: TaskInputs adicionales validan correctamente
    Given CaptureTaskInput vacío
    Then falla por falta de objective
    Given CaptureTaskInput con objective y files
    Then es exitoso
    Given ResearchInput vacío
    Then falla
    Given ResearchInput con topic
    Then es exitoso
    Given KgExtractInput con scope functions
    Then es exitoso
    Given KgExtractInput con scope bogus
    Then falla
    Given ProjectInitInput vacío
    Then falla por falta de project_id
    Given ProjectInitInput con project_id y language
    Then es exitoso

  Scenario: Checkpoint roundtrip preserva tipos
    Given un StepResult con quality_passed=False
    When hago roundtrip JSON
    Then se restaura correctamente
    And quality_passed sigue siendo booleano False

  Scenario: Checkpoint corrupto falla validado
    Given un checkpoint con ok="no-bool"
    When restauro desde checkpoint
    Then falla con error mencionando "ok"
    Given un checkpoint sin marker
    When restauro desde checkpoint
    Then falla

  Scenario: _read_last_step_result lee desde checkpoint
    Given un StepResult con gates fallidas y recommend
    When serializo a checkpoint y lo leo como last_step_result
    Then obtengo quality_passed=False
    And failed_gates incluye cyclomatic_complexity y dead_code
    And advanced_to es CODE_REVIEW_SCAN
    Given un checkpoint corrupto
    When lo leo como last_step_result
    Then obtengo dict vacío

  Scenario: Walker root detecta el repo
    Given el directorio del proyecto
    When busco _walker_root
    Then existe features/cli_interface/mcp.py en el root

  Scenario: mcp_servers_block genera bloque estándar
    Given el directorio del proyecto
    When genero mcp_servers_block
    Then el servidor walker usa comando python3
    And args son ["-m", "features.cli_interface.mcp"]
    And PYTHONPATH incluye el root del walker

  Scenario: generate soporta formatos opencode, claude y cursor
    Given el directorio del proyecto
    When genero para opencode
    Then el bloque tiene type="local" y command[0]="python3"
    When genero para claude
    Then incluye mcpServers
    When genero para cursor
    Then incluye mcpServers

  Scenario: cmd_mcp_config con --raw devuelve JSON válido
    Given el directorio del proyecto
    When ejecuto cmd_mcp_config --raw
    Then el código de salida es 0
    And el JSON incluye "walker" con command="python3"

  Scenario: El servicio detecta root y genera configuración portable
    Given el directorio del proyecto
    When llamo _walker_root y mcp_servers_block
    Then el root existe con mcp.py
    And el servidor walker tiene la configuración esperada
    And el JSON es serializable

  Scenario: handle_mcp_config devuelve respuesta de tipo mcp_config
    Given una Request vacía
    When invoco handle_mcp_config
    Then el tipo es "mcp_config"
    And incluye mcpServers con walker
    And walker tiene command="python3"
    When invoco con raw=true
    Then incluye solo el bloque walker

  Scenario: help incluye mcp-config con formatos documentados
    Given una Request vacía
    When invoco handle_help
    Then el tipo es "help"
    And los comandos incluyen "mcp-config"
    And el usage documenta "format"

  Scenario: La ruta mcp-config está registrada
    Given el router de CLI
    When listo las rutas
    Then "mcp-config" está en los patrones

  Scenario: sprint-status resuelve sprint_id desde la ruta
    Given el router de CLI
    When resuelvo "walker/session-20260731/status"
    Then el handler existe
    And projectId es "walker"
    And sprintId es "session-20260731"
    When creo Request con params de ruta
    Then sprint_id es "session-20260731"
    When creo Request con flags sprint_id
    Then sprint_id es "session-20260804"
