# language: es
# tests/features/auto_query_gate.feature
#
# Feature: Auto-query CKG conventions gate in IMPLEMENT.WORK
#
# Como agente de implementacion
# Quiero consultar automaticamente las convenciones del CKG antes de tocar codigo
# Para garantizar que el codigo producido respeta las decisiones arquitectonicas del proyecto

Característica: Gate de auto-consulta de convenciones CKG en IMPLEMENT.WORK
  Como agente de implementacion
  Quiero consultar automaticamente las convenciones del CKG antes de tocar codigo
  Para garantizar que el codigo producido respeta las decisiones arquitectonicas del proyecto

  Antecedentes:
    Dado que el nodo IMPLEMENT esta definido en core/engine/workflow/nodes/implement.node.json

  @auto-query-gate
  Escenario: WORK declara provider ckg con query type:convention
    Cuando se inspecciona el sub-estado WORK del nodo IMPLEMENT
    Entonces WORK tiene una fuente con provider ckg
    Y la config de esa fuente declara query type:convention

  @auto-query-gate
  Escenario: El focus de WORK ordena auto-consulta CKG
    Cuando se inspecciona el sub-estado WORK del nodo IMPLEMENT
    Cuando se inspecciona el focus de WORK
    Entonces el focus contiene GATE DE AUTO-CONSULTA
    Y el focus contiene kg_multi
    Y el focus contiene ANTES de tocar cualquier archivo

  @auto-query-gate
  Escenario: El prompt implement/work ordena consultar CKG antes de codificar
    Cuando se carga el template implement/work
    Entonces el prompt contiene GATE DE AUTO-CONSULTA DE CONVENCIONES CKG
    Y el prompt contiene walker <project>/kg_schema
    Y el prompt contiene kg_multi
    Y el prompt contiene "ckg" y "type" y "convention" y "scope"
    Y el gate aparece antes de CALIDAD DEL CODIGO

  @auto-query-gate
  Escenario: El template WORK no rompe placeholders existentes
    Cuando se carga el template implement/work
    Y se renderiza con datos de paso
    Entonces el render no deja placeholders sin resolver
    Y el render mantiene el GATE DE AUTO-CONSULTA

  @auto-query-gate
  Escenario: ContextComposer inyecta la capa ckg en WORK
    Dado un proyecto temporal con un CKG minimo
    Cuando se compone el contexto para WORK con ContextComposer
    Entonces el system_message contiene la instruccion de la capa ckg
    Y la capa ckg contiene convenciones consultadas
    Y el prompt renderizado mantiene el gate

  @auto-query-gate
  Escenario: DESIGN_GATHER declara las 4 capas de conocimiento
    Dado que el nodo DESIGN esta definido en core/engine/workflow/nodes/design.node.json
    Cuando se inspecciona el sub-estado DESIGN_GATHER del nodo DESIGN
    Entonces sus fuentes incluyen business, ckg, cpg y delta

  @auto-query-gate
  Escenario: El prompt DESIGN_GATHER describe el gate y las capas
    Dado que el nodo DESIGN esta definido en core/engine/workflow/nodes/design.node.json
    Cuando se carga el template design/design-gather
    Entonces el prompt contiene GATE DE AUTO-CONSULTA
    Y el prompt describe << ckg >>, << cpg >> y << delta >>
    Y el prompt contiene kg_schema y kg_multi

  @auto-query-gate
  Escenario: ContextComposer inyecta capas en DESIGN_GATHER end-to-end
    Dado un proyecto temporal con un CKG minimo
    Dado que el nodo DESIGN esta definido en core/engine/workflow/nodes/design.node.json
    Cuando se compone el contexto para DESIGN_GATHER con ContextComposer
    Entonces las capas renderizadas incluyen ckg
    Y el prompt renderizado mantiene el GATE DE AUTO-CONSULTA
