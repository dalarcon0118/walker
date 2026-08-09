# language: es
# tests/features/context_contracts.feature
#
# Feature: Context contracts
#
# Como motor del workflow
# Quiero que cada provider de contexto exponga un schema JSON coherente
# Para que el agente reciba tipos correctos y el compositor pueda ensamblar contratos

Característica: Contratos de contexto tipados por provider
  Como motor del workflow
  Quiero que cada provider de contexto exponga un schema JSON coherente
  Para que el agente reciba tipos correctos y el compositor pueda ensamblar contratos

  Antecedentes:
    Dado que existen 9 providers de contexto declarados en los registros

  @context-contracts
  Escenario: CONTEXT_SCHEMAS registra un schema por cada provider
    Cuando se consulta CONTEXT_SCHEMAS
    Entonces los 9 providers tienen un schema registrado
    Y cada schema declara type object

  @context-contracts
  Escenario: context_schema es fail-open para provider desconocido
    Cuando se consulta context_schema para un provider imaginario
    Entonces retorna {}
    Cuando se consulta context_schema para cadena vacia
    Entonces retorna {}
    Cuando se consultan todos los context_schemas
    Entonces all_context_schemas retorna exactamente los 9 providers

  @context-contracts
  Escenario: Cada schema es coherente con el contenido de su capa
    Cuando se inspecciona el schema de cpg
    Entonces cpg incluye files y symbols
    Cuando se inspecciona el schema de diff
    Entonces diff incluye base_ref y files
    Cuando se inspecciona el schema de test_results
    Entonces test_results incluye passed y failed
    Cuando se inspecciona el schema de trace
    Entonces trace incluye events y tool

  @context-contracts
  Escenario: El compositor puebla context_schemas desde output_schema del provider
    Dado un provider stub con output_schema
    Cuando se compone el contexto
    Entonces el ComposedContext incluye el schema del provider
    Y el schema tiene packages tipo array

  @context-contracts
  Escenario: El compositor hace fallback a CONTEXT_SCHEMAS si el provider no implementa output_schema
    Dado un provider stub sin output_schema
    Cuando se compone el contexto
    Entonces el ComposedContext incluye el schema del provider desde CONTEXT_SCHEMAS
    Y el schema es el mismo objeto registrado en CONTEXT_SCHEMAS

  @context-contracts
  Escenario: workflow.json declara context_schema de los 9 providers
    Cuando se carga workflow.json
    Entonces la seccion context_schema.providers incluye los 9 providers
