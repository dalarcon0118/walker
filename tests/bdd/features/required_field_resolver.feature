# language: es
# tests/features/required_field_resolver.feature
#
# Feature: Required field resolver
#
# Como motor del workflow
# Quiero derivar automaticamente los campos requeridos de un sub-estado
# Para que el schema sea la fuente de verdad y no haya campos legacy desincronizados

Característica: Derivación de campos requeridos en sub-estados
  Como arquitecto del workflow engine
  Quiero que los required_fields se deriven del JSON Schema
  Para eliminar el campo legacy required_fields de node.json v3

  @contrato
  Escenario: input_schema.required tiene prioridad sobre output_schema
    Dado un sub-estado con input_schema que requiere decision
    Cuando se derivan los campos requeridos
    Entonces los campos requeridos son decision

  @contrato
  Escenario: Sin input_schema.required, usa output_schema.required
    Dado un sub-estado sin input_schema.required
    Y el sub-estado tiene output_schema que requiere captured
    Cuando se derivan los campos requeridos
    Entonces los campos requeridos son captured

  @contrato
  Escenario: Sin schemas, usa legacy required_fields
    Dado un sub-estado sin input_schema ni output_schema
    Y el sub-estado tiene legacy required_fields con goal y tasks
    Cuando se derivan los campos requeridos
    Entonces los campos requeridos son goal y tasks

  @contrato
  Escenario: Sin schemas ni legacy, retorna vacio
    Dado un sub-estado sin input_schema ni output_schema ni legacy
    Cuando se derivan los campos requeridos
    Entonces los campos requeridos estan vacios

  @contrato
  Escenario: None retorna vacio sin lanzar
    Dado un sub-estado None
    Cuando se derivan los campos requeridos
    Entonces los campos requeridos estan vacios

  @contrato
  Escenario: SPRINT.PLANNING deriva goal y tasks desde input_schema
    Dado un sub-estado SPRINT.PLANNING con input_schema que requiere goal y tasks
    Cuando se derivan los campos requeridos
    Entonces los campos requeridos incluyen goal
    Y los campos requeridos incluyen tasks

  @contrato
  Escenario: ANALYZE.ANALYZE deriva 6 campos desde input_schema
    Dado un sub-estado ANALYZE.ANALYZE con input_schema que requiere q1, a1, q2, a2, q3 y a3
    Cuando se derivan los campos requeridos
    Entonces los campos requeridos incluyen q1, a1, q2, a2, q3 y a3

  @contrato
  Escenario: resolve_required es alias de derive
    Dado un sub-estado con input_schema que requiere decision
    Cuando se resuelven los campos requeridos con resolve_required
    Entonces los campos requeridos son decision

