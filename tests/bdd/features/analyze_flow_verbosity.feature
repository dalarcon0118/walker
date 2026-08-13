# language: es
# tests/features/analyze_flow_verbosity.feature
#
# Feature: Analyze flow verbosity reduction
#
# Como motor del workflow
# Quiero que el flujo ANALYZE fusione KNOWLEDGE_REVIEW en REVIEW
# Para reducir la verbosidad y los user_input redundantes del PO

Característica: Reduccion de verbosidad en el flujo ANALYZE
  Como motor del workflow
  Quiero que el flujo ANALYZE fusione KNOWLEDGE_REVIEW en REVIEW
  Para reducir la verbosidad y los user_input redundantes del PO

  Antecedentes:
    Dado que el nodo ANALYZE esta definido en core/engine/workflow/nodes/analyze.node.json

  @analyze
  Escenario: KNOWLEDGE_REVIEW ya no existe en el nodo ANALYZE
    Cuando se cargan los sub-estados del nodo ANALYZE
    Entonces KNOWLEDGE_REVIEW no esta entre las claves de sub-estados

  @analyze
  Escenario: REVIEW ahora captura knowledge_items en su output_schema
    Cuando se inspecciona el sub-estado REVIEW
    Entonces su output_schema incluye knowledge_items
    Y su output_schema incluye decision
    Y su output_schema incluye evidence

  @analyze
  Escenario: KNOWLEDGE_CAPTURE es fin de nodo
    Cuando se inspecciona el sub-estado KNOWLEDGE_CAPTURE
    Entonces su on_success es vacio o nulo

  @analyze
  Escenario: El grafo de workflow valida sin warnings
    Dado que existe un spring de session-20260731 cargado
    Cuando se ejecuta verify_graph sobre el workflow
    Entonces el grafo es valido
    Y no hay warnings

  @analyze
  Escenario: REVIEW.approve mapea a analysis_approved
    Cuando se consulta el mapeo de decision gates de REVIEW
    Entonces approve se resuelve como analysis_approved
