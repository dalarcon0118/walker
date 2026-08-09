# language: es
# tests/features/uncaptured_work_gate.feature
#
# Feature: Uncaptured work gate
#
# Como usuario del workflow engine
# Quiero que el trabajo sin capturar se detecte automáticamente
# Para que el sprint no avance con trabajo invisible

Característica: Gate de trabajo sin capturar
  Como usuario del workflow engine
  Quiero que el trabajo sin capturar se detecte automáticamente
  Para que el sprint no avance con trabajo invisible

  @contrato
  Escenario: Cuando no hay trabajo sin capturar, el resultado muestra limpieza y permite el avance
    Dado un resultado limpio sin trabajo sin capturar
    Cuando se consulta el estado del resultado
    Entonces el estado es limpio
    Y no bloquea el avance

  @contrato
  Escenario: Cuando hay trabajo sin capturar, el resultado indica bloqueo con un mensaje accionable
    Dado un resultado con trabajo sin capturar en archivos específicos
    Cuando se consulta el estado del resultado
    Entonces el estado indica trabajo sin capturar
    Y bloquea el avance con un mensaje accionable
    Y el mensaje incluye los archivos afectados
    Y el mensaje sugiere capturar el trabajo

  @contrato
  Escenario: Cuando la detección falla, el resultado muestra el error sin bloquear el flujo
    Dado un resultado de error de detección
    Cuando se consulta el estado del resultado
    Entonces el estado indica error
    Y no bloquea el avance
    Y la detección no oculta el fallo

  @contrato
  Escenario: El detector identifica trabajo invisible comparando cambios contra especificaciones
    Dado un proyecto con archivos modificados cubiertos por sus especificaciones
    Cuando se ejecuta la detección de trabajo sin capturar
    Entonces el detector determina que no hay trabajo invisible
    Y no bloquea el avance del sprint

  @contrato
  Escenario: El detector identifica trabajo sin capturar cuando faltan especificaciones
    Dado un proyecto con trabajo sin capturar
    Cuando se ejecuta la detección de trabajo sin capturar
    Entonces el detector identifica el trabajo sin capturar
    Y los archivos sin capturar son solo los desprotegidos
    Y bloquea el avance del sprint

  @contrato
  Escenario: Una especificación que cubre un directorio protege todos sus archivos
    Dado un proyecto donde una especificación cubre un directorio completo
    Cuando se ejecuta la detección de trabajo sin capturar
    Entonces el detector determina que no hay trabajo invisible
    Y no bloquea el avance del sprint

  @contrato
  Escenario: Sin sprint activo, el detector no bloquea el avance
    Dado un proyecto sin sprint activo
    Cuando se ejecuta la detección de trabajo sin capturar
    Entonces el detector determina que no hay trabajo invisible
    Y no hay spring activo
    Y no bloquea el avance del sprint

  @contrato
  Escenario: El detector usa el registro de cambios del watcher cuando no hay eventos de trace
    Dado un proyecto con cambios del watcher pero sin eventos de trace
    Cuando se ejecuta la detección de trabajo sin capturar
    Entonces los archivos afectados incluyen los cambios del watcher
    Y el resultado se puede serializar a diccionario

  @gate
  Escenario: Al cerrar el sprint, la gate bloquea el avance cuando hay trabajo sin capturar
    Dado un sprint en revisión con trabajo sin capturar
    Cuando se intenta avanzar el sprint
    Entonces el sprint queda bloqueado
    Y el tipo de error es de gate de trabajo sin capturar
    Y no se ejecuta el submit
    Y el mensaje incluye los archivos afectados

  @gate
  Escenario: Cuando todo el trabajo está capturado, el sprint avanza al cerrar
    Dado un sprint en revisión con todo el trabajo capturado
    Cuando se intenta avanzar el sprint
    Entonces el sprint avanza normalmente
    Y se ejecuta el submit

  @gate
  Escenario: El gate no bloquea el trabajo normal fuera del sprint
    Dado el workflow en un nodo normal de trabajo
    Cuando se intenta avanzar el flujo
    Entonces el sprint avanza normalmente
    Y la gate no aplica fuera del sprint

  @gate
  Escenario: El gate solo se activa al cerrar el sprint, no en sub-estados intermedios
    Dado el workflow en un sub-estado intermedio del sprint
    Cuando se intenta avanzar el flujo
    Entonces el sprint avanza normalmente
    Y la gate no aplica en sub-estados intermedios
