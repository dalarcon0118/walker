# language: es
# tests/features/knowledge_capture_schema.feature
#
# Feature: Schema canónico de captura de conocimiento (SPEC-004)
#
# Como motor del workflow
# Quiero que los schemas de captura de conocimiento referencien un schema canónico
# Para evitar divergencias entre nodos del workflow

Característica: Schema canónico de captura de conocimiento (SPEC-004)
  Como motor del workflow
  Quiero que los schemas de knowledge capture referencien un schema canónico
  Para evitar divergencias entre nodos y garantizar coherencia

  @canonico
  Escenario: El schema canónico existe y es JSON válido
    Dado el archivo knowledge-capture.schema.json
    Cuando se carga el schema
    Entonces el schema tiene $defs
    Y knowledge_item está definido en $defs

  @canonico
  Escenario: knowledge_item define type, domain, statement y rationale
    Dado el archivo knowledge-capture.schema.json
    Cuando se inspecciona knowledge_item
    Entonces tiene propiedades type, domain, statement y rationale
    Y type está en required

  @refs
  Escenario: ANALYZE.KNOWLEDGE_CAPTURE referencia el schema canónico
    Dado el nodo analyze.node.json
    Cuando se busca el sub-estado KNOWLEDGE_CAPTURE
    Entonces su output_schema tiene $ref al canónico

  @refs
  Escenario: REFINE.KNOWLEDGE_CAPTURE referencia el schema canónico
    Dado el nodo refine.node.json
    Cuando se busca el sub-estado KNOWLEDGE_CAPTURE
    Entonces su output_schema tiene $ref al canónico

  @refs
  Escenario: IMPLEMENT.KNOWLEDGE_UPDATE referencia el schema canónico
    Dado el nodo implement.node.json
    Cuando se busca el sub-estado KNOWLEDGE_UPDATE
    Entonces su output_schema tiene $ref al canónico

  @refs
  Escenario: SPRINT.RETRO referencia el schema canónico
    Dado el nodo sprint.node.json
    Cuando se busca el sub-estado RETRO
    Entonces su output_schema tiene $ref al canónico

  @carga
  Escenario: El workflow carga sin errores con schemas con $ref
    Dado un directorio de spring con schemas que usan $ref
    Cuando se carga el workflow desde el repositorio
    Entonces el workflow tiene 8 nodos
