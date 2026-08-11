# language: es
# tests/features/intelligence_service.feature
#
# Feature: Orquestador de joins (ADR-WALKER-023)
#
# Como servicio de inteligencia
# Quiero cruzar las 4 capas por archivo/task_id
# Para obtener contexto completo sin tocar CognitiveGraph directamente

Característica: Orquestador de joins de inteligencia (ADR-WALKER-023)
  Como servicio de inteligencia de tareas
  Quiero cruzar CPG, CKG, Delta y Trace por archivo o task_id
  Para obtener contexto completo sin tocar CognitiveGraph directamente

  @joins
  Escenario: Cruzar por archivo devuelve símbolos, specs, conocimiento y traces
    Dado un CPG con símbolos en src/auth.py
    Y un CKG con nodos de convención en auth
    Y un Delta con specs para src/auth.py y src/other.py
    Y un Trace con eventos en src/auth.py
    Cuando se consulta inteligencia por archivo src/auth.py
    Entonces el resultado es exitoso
    Y CPG tiene 2 símbolos (login, AuthService)
    Y Delta tiene 1 item filtrado por archivo
    Y CKG tiene 1 nodo
    Y Trace tiene 1 evento
    Y el payload type es task_intelligence

  @joins
  Escenario: Cruzar por task_id filtra delta y trace correctamente
    Dado un Trace con eventos de TASK-042 y TASK-999
    Cuando se consulta inteligencia por task_id TASK-042
    Entonces el resultado es exitoso
    Y Trace tiene 1 evento con task_id TASK-042
    Y Delta está disponible

  @joins
  Escenario: Fallo de un repo degrada su sección sin tirar todo el resultado
    Dado un CPG que lanza RuntimeError
    Cuando se consulta inteligencia por archivo
    Entonces el resultado es exitoso (convención: nunca lanza)
    Y CPG está degradado con ok False
    Y CKG, Delta y Trace siguen vivas

  @joins
  Escenario: task_id enriquece delta con GetTaskContextUseCase
    Dado un Spring con goal y un Spec con dependencias
    Cuando se consulta inteligencia por task_id y archivo
    Entonces el Delta tiene 1 item
    Y el item tiene spring_goal enriquecido
    Y dependencies es una lista

  @joins
  Escenario: Scope con archivo setea summary con totales
    Dado el servicio de inteligencia
    Cuando se consulta inteligencia por archivo
    Entonces el scope.file es src/auth.py
    Y el summary tiene claves cpg_total, ckg_total, delta_total y trace_total

  @use_cases
  Escenario: task_file_intelligence devuelve Result con símbolos y specs
    Dado un CPG con un símbolo login en src/auth.py
    Y un Delta con spec Agregar login para src/auth.py
    Cuando se ejecuta task_file_intelligence
    Entonces el resultado es exitoso
    Y el file es src/auth.py
    Y symbols tiene login
    Y specs tiene Agregar login
    Y risk es LOW, MED o HIGH
    Y el payload type es task_file_intelligence

  @use_cases
  Escenario: task_file_intelligence repo fallido degrada sección
    Dado un CPG que lanza RuntimeError
    Cuando se ejecuta task_file_intelligence
    Entonces el resultado es exitoso (convención: nunca lanza)
    Y el file se preserva
    Y risk es LOW, MED o HIGH

  @use_cases
  Escenario: failure_diagnosis devuelve diagnóstico con herramientas usadas
    Dado un Trace con eventos edit.file y submit para TASK-042
    Cuando se ejecuta failure_diagnosis
    Entonces el resultado es exitoso
    Y tools_used incluye edit.file
    Y el diagnosis menciona actividad reciente
    Y el payload type es failure_diagnosis

  @use_cases
  Escenario: qa_coverage devuelve Result con phase VERIFY
    Dado el servicio de inteligencia
    Cuando se ejecuta qa_coverage para TASK-042
    Entonces el resultado es exitoso
    Y el payload phase es VERIFY

  @use_cases
  Escenario: sprint_evidence devuelve Result con phase SPRINT.PLANNING
    Dado el servicio de inteligencia
    Cuando se ejecuta sprint_evidence con goal agregar auth
    Entonces el resultado es exitoso
    Y el payload phase es SPRINT.PLANNING
