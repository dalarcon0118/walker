# language: es
# tests/features/adr_schema.feature
#
# Feature: ADR schema integrity
#
# Como mantenedor de arquitectura
# Quiero que los ADRs cumplan un schema estricto enriquecido para agentes IA
# Para garantizar la integridad estructural y el consumo automático de conocimiento

Característica: Integridad estructural de los ADRs
  Como mantenedor de arquitectura
  Quiero que los ADRs cumplan un schema estricto enriquecido
  Para garantizar la integridad estructural y el consumo automático por agentes de IA

  Antecedentes:
    Dado que el directorio schemas/adrs/ contiene ADRs JSON

  @adr
  Escenario: El schema ADR es JSON válido de tipo object
    Cuando se carga el schema adr-walker.schema.json
    Entonces el schema es JSON válido
    Y el schema declara type object
    Y el schema declara campos required

  @adr
  Escenario: Cada ADR contiene los campos requeridos para agentes IA
    Dado que existen ADRs en schemas/adrs/
    Cuando se valida cada ADR contra los campos requeridos
    Entonces cada ADR tiene id, status, date, summary, problem_statement, decision e impact

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
  Escenario: decision e impact tienen la estructura correcta para razonamiento automático
    Dado que existen ADRs en schemas/adrs/
    Cuando se valida la estructura de decision e impact
    Entonces decision.statement no esta vacío
    Y decision.rationale no esta vacío
    Y impact.affected_components es una lista
    Y impact.changes es una lista

  @adr
  Escenario: implementation tiene steps con files y verification
    Dado que existen ADRs en schemas/adrs/
    Cuando se valida cada paso de implementacion
    Entonces cada paso tiene descripcion
    Y si existe files, es una lista de strings
    Y si existe verification, es un string no vacío
    Y si existe done, es booleano

  @adr
  Escenario: Existe validation_checklist con comandos BDD ejecutables
    Dado que existen ADRs en schemas/adrs/
    Cuando se valida el campo validation_checklist
    Entonces validation_checklist es una lista de objetos con description, command y expected
    Y cada item tiene description, command y expected no vacíos

  @adr
  Escenario: Existe al menos un ADR JSON
    Cuando se carga el listado de ADRs
    Entonces se encuentra al menos un ADR JSON
