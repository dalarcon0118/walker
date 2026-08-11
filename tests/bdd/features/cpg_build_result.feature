# language: es
# tests/features/cpg_build_result.feature
#
# Feature: Result pattern en el build del CPG (features/code_analysis/application/build_service.py).
#
# _sync_core (@safe) sincroniza nodos CPG al índice cpg del CKG. Ante data
# inválido devuelve Failure (no lanza) y el wrapper de instancia loggea
# (convention-001: nunca except silencioso).

Característica: Result pattern en el build del CPG
  Como arquitecto del módulo CPG
  Quiero que la sincronización al índice cpg siga el result pattern
  Para componer errores sin excepciones silenciosas

  @contrato
  Escenario: Sincronización de nodos válidos al índice cpg
    Dado un builder del CPG con 2 nodos válidos en su grafo
    Cuando se sincronizan los nodos al índice cpg
    Entonces el resultado es un Success de returns
    Y quedan 2 nodos disponibles en el índice cpg

  @contrato
  Escenario: Nodo inválido produce Failure y log no silencioso
    Dado un builder del CPG con un nodo con datos inválidos
    Cuando se sincroniza el nodo al índice cpg
    Entonces el resultado es un Failure de returns
    Y el wrapper de instancia loggea el error