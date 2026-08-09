# language: es
# tests/features/adr_schema.feature
#
# Feature: ADR schema integrity
#
# Como mantenedor de arquitectura
# Quiero que los ADRs cumplan un schema estricto
# Para garantizar la integridad estructural de las decisiones arquitectónicas

Característica: Integridad estructural de los ADRs
  Como mantenedor de arquitectura
  Quiero que los ADRs cumplan un schema estricto
  Para garantizar la integridad estructural de las decisiones arquitectónicas

  Antecedentes:
    Dado que el directorio schemas/adrs/ contiene ADRs JSON

  @adr
  Escenario: El schema ADR es JSON válido de tipo object
    Cuando se carga el schema adr-walker.schema.json
    Entonces el schema es JSON válido
    Y el schema declara type object
    Y el schema declara campos required

  @adr
  Escenario: Cada ADR contiene los campos requeridos
    Dado que existen ADRs en schemas/adrs/
    Cuando se valida cada ADR contra los campos requeridos
    Entonces cada ADR tiene id, status, date, summary, context, decision e impact

  @adr
  Escenario: Cada ADR tiene id y status válidos
    Dado que existen ADRs en schemas/adrs/
    Cuando se valida el formato del id
    Entonces el id cumple el patron ADR-WALKER-\d{3}
    Y el status pertenece al conjunto de estados válidos

  @adr
  Escenario: Cada relacion related es válida
    Dado que existen ADRs en schemas/adrs/
    Cuando se valida cada relacion related
    Entonces cada relacion tiene id
    Y cada relation pertenece al conjunto de relaciones válidas

  @adr
  Escenario: decision e impact tienen la estructura correcta
    Dado que existen ADRs en schemas/adrs/
    Cuando se valida la estructura de decision e impact
    Entonces decision.statement no esta vacío
    Y impact.changes es una lista

  @adr
  Escenario: implementation_steps tienen descripcion y done booleano
    Dado que existen ADRs en schemas/adrs/
    Cuando se valida cada paso de implementacion
    Entonces cada paso tiene descripcion
    Y si existe done, es booleano

  @adr
  Escenario: Existe al menos un ADR JSON
    Cuando se carga el listado de ADRs
    Entonces se encuentra al menos un ADR JSON
