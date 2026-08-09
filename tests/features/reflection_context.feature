# language: es
# tests/features/reflection_context.feature
#
# Feature: Reflexión por contexto (REFINE.DIAGNOSE).
#
# El motor es un mecanismo puro; la reflexión es lógica de la feature
# event_tracing. build_reflection_context deriva source_node/attempt desde
# el TraceGraph, compose error_context desde el estado, y el ReflectionProvider
# produce una ContextPiece con la capa 'reflection'.

Característica: Reflexión por contexto en el ciclo REFINE
  Como motor de workflow
  Quiero derivar source_node/attempt desde el trace y componer contexto de reflexión
  Para que el agente tenga información determinista sin depender del motor

  @contrato
  Escenario: build_reflection_context deriva source_node desde TRANSITION a REFINE
    Dado un proyecto walker con trace NDJSON
    Y el trace tiene TRANSITION desde IMPLEMENT hacia REFINE
    Cuando construyo el contexto de reflexión para la tarea
    Entonces source_node es IMPLEMENT
    Y attempt es 1
    Y max_attempts es 3

  @contrato
  Escenario: build_reflection_context incrementa attempt con segundo REFINE
    Dado un proyecto walker con trace NDJSON
    Y el trace tiene dos TRANSITION hacia REFINE
    Cuando construyo el contexto de reflexión para la tarea
    Entonces attempt es 2
    Y el source_node es el del último refine

  @contrato
  Escenario: ReflectionProvider produce ContextPiece con capa reflection
    Dado un proyecto walker con trace NDJSON
    Y el trace tiene TRANSITION desde VERIFY hacia REFINE
    Y el contexto incluye last_error y last_error_strategy
    Cuando el ReflectionProvider recolecta el contexto
    Entonces la pieza tiene provider reflection
    Y el contenido incluye source_node y attempt
    Y el contenido incluye el error reportado

  @contrato
  Escenario: El registry descubre reflection sin tocar el engine
    Dado el bootstrap de providers del engine
    Cuando consulto los providers disponibles
    Entonces reflection está registrado

  @contrato
  Escenario: PromptContextBuilder inyecta claves de reflexión
    Dado un proyecto walker con trace NDJSON
    Y el trace tiene TRANSITION desde IMPLEMENT hacia REFINE
    Cuando el PromptContextBuilder construye el contexto
    Entonces el contexto incluye source_node
    Y el contexto incluye attempt y max_attempts
    Y el contexto incluye error_context y hint
    Y el contexto incluye reflection_past_attempts

  @contrato
  Escenario: resolve_prompt resuelve placeholders de reflexión
    Dado un proyecto walker con trace NDJSON
    Y el template de refine incluye placeholders
    Y el trace tiene TRANSITION desde VERIFY hacia REFINE
    Cuando el OrchestrationService resuelve el prompt
    Entonces source_node está resuelto en el prompt
    Y hint está resuelto en el prompt
