# language: es
# tests/features/cpg_use_cases.feature
#
# Feature: Use cases de aplicación del CPG (features/code_analysis/application/use_cases.py).
#
# Cubre los 5 use cases públicos: CheckCpgHealthUseCase, GatherCpgContextUseCase,
# DetectUnusedFunctionsUseCase, ReviewCodeUseCase y ProjectMetricsUseCase.
# Contrato: nunca lanzan, devuelven dict con keys esperadas (convention-001).

Característica: Use cases de aplicación del CPG
  Como arquitecto del módulo CPG
  Quiero que los use cases públicos devuelvan estructuras deterministas
  Para que tasks y agentes consuman el CPG sin conocer su formato interno

  @contrato
  Escenario: Health check de un CPG indexado
    Dado un proyecto walker con CPG construido
    Cuando se consulta la salud del CPG
    Entonces el status termina en "nodes"
    Y node_count es mayor que cero
    Y el número del status coincide con node_count

  @contrato
  Escenario: Health check sin CPG indexado
    Dado un directorio de datos sin CPG
    Cuando se consulta la salud del CPG
    Entonces el status es "not scanned"
    Y node_count es cero

  @contexto
  Escenario: Gather de contexto sin files devuelve estructura vacía
    Dado un proyecto walker con CPG construido
    Cuando se hace gather de contexto sin files
    Entonces no hay error en el resultado
    Y files_analyzed es cero
    Y la key "structure" está presente

  @contexto
  Escenario: Gather de contexto con un file analiza ese file
    Dado un proyecto walker con CPG construido
    Cuando se hace gather de contexto con el file "src/mod.py"
    Entonces no hay error en el resultado
    Y files_analyzed es uno
    Y files es un dict

  @bugs
  Escenario: Detección de funciones sin uso
    Dado un proyecto walker con CPG construido
    Cuando se detectan funciones sin uso
    Entonces el resultado expone la query "bug:unused_functions"
    Y el total es un entero
    Y no hay errores

  @review
  Escenario: Review de código devuelve calidad y bug proposals
    Dado un chain_dir del proyecto walker registrado
    Cuando se ejecuta el review de código
    Entonces el reporte incluye bug_proposals
    Y bug_proposals expone total_findings entero
    Y bug_proposals incluye by_type y findings
    Y second_report es coherente con bug_proposals

  @metrics
  Escenario: Métricas de proyecto y quality gates
    Dado un chain_dir del proyecto walker registrado
    Cuando se computan las métricas del proyecto
    Entonces el resultado incluye metrics y gates
    Y las métricas reportan archivos y LOC mayores a cero
    Y los gates incluyen cyclomatic_complexity y dead_code
    Y cada gate expone threshold, actual y passed
