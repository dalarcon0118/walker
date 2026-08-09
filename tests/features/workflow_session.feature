# language: es
# tests/features/workflow_session.feature
#
# Feature: Reuso de sesión por chain_dir en WorkflowAPI.
#
# Cada invocación del CLI/MCP con un chain_dir explícito (p.ej. status de
# sprint) creaba una sesión nueva en .cognitive/sessions/ en lugar de
# reutilizar la existente, acumulando archivos duplicados por llamada.
# La sesión se reutiliza si existe para el mismo chain_dir y no está stale;
# solo se crea una nueva cuando no hay reutilizable.

Característica: WorkflowAPI reutiliza la sesión de un chain_dir
  Como motor del walker
  Quiero reutilizar la sesión del chain_dir activo
  Para no acumular archivos duplicados en <sessions_dir>

  @contrato
  Escenario: Primera llamada con chain_dir crea la sesión
    Dado un sessions_dir temporal vacío
    Y un WorkflowAPI con chain_dir explícito
    Cuando aseguro sesión
    Entonces el sessions_dir tiene 1 sesión
    Y la sesión apunta al chain_dir dado
    Y la sesión queda como activa

  @contrato
  Escenario: Segunda llamada con el mismo chain_dir reutiliza la sesión
    Dado un sessions_dir temporal vacío
    Y un WorkflowAPI con chain_dir explícito
    Cuando aseguro sesión
    Entonces el sessions_dir tiene 1 sesión
    Cuando un nuevo WorkflowAPI con el mismo chain_dir asegura sesión
    Entonces el sessions_dir tiene 1 sesión
    Y la sesión activa es la misma

  @contrato
  Escenario: chain_dir distinto no comparte sesión
    Dado un sessions_dir temporal vacío
    Y un WorkflowAPI con chain_dir explícito
    Cuando aseguro sesión
    Cuando un nuevo WorkflowAPI con otro chain_dir asegura sesión
    Entonces el sessions_dir tiene 2 sesiones