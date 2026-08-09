# language: es
# tests/features/feedback_coherence.feature
#
# Feature: Coherencia del feedback del workflow
#
# Como agente del sistema
# Quiero recibir feedback coherente y con contexto cuando algo falla
# Para saber exactamente qué falló y cómo corregirlo

Característica: Coherencia del feedback del workflow
  Como agente del sistema
  Quiero que los errores del workflow incluyan contexto completo
  Para saber exactamente qué falló y cómo corregirlo

  @error_handler
  Escenario: BLOCK_MESSAGE incluye details de precondiciones fallidas
    Dado un TransitionBlockedError con details de precondiciones
    Cuando el ErrorHandler maneja el error
    Entonces el resultado tiene ok False y strategy block+message
    Y el mensaje incluye los detalles específicos
    Y los detalles están en el resultado

  @error_handler
  Escenario: BLOCK_MESSAGE incluye errors de campos inválidos
    Dado un ValidationError con errors de campos
    Cuando el ErrorHandler maneja el error
    Entonces el resultado tiene ok False
    Y el mensaje incluye los nombres de campos con error
    Y los errors están en los detalles

  @error_handler
  Escenario: BLOCK_MESSAGE incluye stderr de validación de gate
    Dado un GateValidationError con stderr
    Cuando el ErrorHandler maneja el error
    Entonces el resultado tiene ok False
    Y el mensaje incluye el stderr
    Y stderr está en los detalles

  @error_handler
  Escenario: ValidationError genérica sin contexto funciona correctamente
    Dado un ValidationError genérico sin contexto
    Cuando el ErrorHandler maneja el error
    Entonces el resultado tiene ok False
    Y el mensaje es exactamente "Algo salió mal"
    Y los detalles están presentes como dict

  @validate_plan
  Escenario: validate_plan reporta errores cuando falla
    Dado un ExecutionContext y un delta con specs en draft y vacías
    Cuando se ejecuta validate_plan
    Entonces el resultado tiene ok False
    Y tiene summary
    Y los errores están en metadata.errors

  @validate_plan
  Escenario: validate_plan tiene éxito con specs válidos
    Dado un ExecutionContext y un delta con specs válidas
    Cuando se ejecuta validate_plan
    Entonces el resultado tiene ok True
    Y metadata.errors está vacío

  @validate_plan
  Escenario: validate_plan falla cuando el spring no está en el delta
    Dado un ExecutionContext y un spring no encontrado
    Cuando se ejecuta validate_plan
    Entonces el resultado tiene ok False
    Y tiene summary

  @submit_orchestrator
  Escenario: Se detectan side-effects fallidas en la lista
    Dado una lista de side-effects con una fallida
    Cuando se filtran las fallidas
    Entonces se detecta 1 side-effect fallida
    Y es de tipo validate_plan

  @submit_orchestrator
  Escenario: Side-effect fallida tiene lista de errores
    Dado una side-effect fallida con errors
    Cuando se accede a errors
    Entonces hay 2 errores
    Y contienen los mensajes específicos de SPEC-001

  @submit_orchestrator
  Escenario: Side-effects exitosos no se marcan como errores
    Dado una lista de side-effects todas exitosas
    Cuando se filtran las fallidas
    Entonces no hay side-effects fallidas
