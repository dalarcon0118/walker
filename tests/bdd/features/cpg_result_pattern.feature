# language: es
# tests/features/cpg_result_pattern.feature
#
# Feature: Result pattern en el módulo CPG (features/code_analysis).
#
# Contexto: la migración a result pattern (returns.Result) es parcial.
# build_service.py ya usa @safe + Result, pero query_service, bug_service
# y los adapters devuelven dict|None y lanzan excepciones. Estos escenarios
# describen el CONTRATO que toda operación del CPG debe cumplir una vez
# migrada.

Característica: Result pattern en las operaciones del CPG
  Como arquitecto del módulo CPG
  Quiero que toda operación que pueda fallar devuelva Result[Success, Failure]
  Para componer errores sin excepciones silenciosas y decidir por el tipo

  Antecedentes:
    Dado un entorno de trabajo del CPG aislado en un directorio temporal

  @contrato
  Escenario: La sincronización de nodos al índice CPG tiene éxito
    Dado un builder del CPG con 2 nodos de ejemplo en su grafo
    Cuando se sincronizan los nodos al índice cpg
    Entonces el resultado es un Success de la librería returns
    Y los 2 nodos quedan disponibles en el índice cpg

  @contrato
  Escenario: La operación fallida devuelve Failure en lugar de lanzar
    Dado un builder del CPG con un nodo de datos inválidos
    Cuando se sincroniza el nodo al índice cpg
    Entonces el resultado es un Failure de la librería returns
    Y el failure encapsula la excepción original (tipo y mensaje)
    Y el wrapper de instancia registra el error en el logger, no en silencio

  @composicion
  Escenario: Composición de operaciones sin try/except
    Dado un resultado Success de una operación del CPG
    Cuando se decide el camino con la función de chequeo de la librería returns
    Entonces el valor se extrae sin lanzar excepción
    Y ante un Failure se obtiene un valor por defecto con value_or
    Y la orquestación no usa cláusulas except para manejar el error

  @excepciones
  Escenario: Las excepciones del core puro se envuelven en Failure
    Dado un core puro del CPG que lanza al recibir entrada inválida
    Cuando la ejecución vía @safe envuelve la excepción
    Entonces el caller recibe un Failure en vez de que se propague
    Y el tipo original de la excepción se preserva en el failure

## Notas de implementación
# - Result pattern: dry-python/returns (Success/Failure)
# - Los cores puros con @safe nunca lanzan: la excepción se convierte en Failure
# - El wrapper de instancia decide con is_successful y hace fallback explícito
