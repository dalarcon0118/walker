# language: es
# tests/features/cpg_hallucination.feature
#
# Feature: Detección de imports alucinados (features/code_analysis/infrastructure/hallucination.py).
#
# Un import alucinado es aquel que referencia un módulo/símbolo inexistente
# en el proyecto ni en stdlib/third-party. El detector devuelve issues con
# status HALLUCINATION, respetando los imports válidos.

Característica: Detección de imports alucinados
  Como arquitecto del módulo CPG
  Quiero detectar imports que referencian módulos o símbolos inexistentes
  Para evitar que el agente importe código alucinado

  @contrato
  Escenario: Detecta import inexistente y respeta el válido
    Dado un directorio con un módulo definido y un test que lo importa
    Y el test importa un paquete inexistente
    Cuando se escanea el archivo test
    Entonces se detecta al menos una alucinación
    Y el módulo alucinado es "nonexistent_pkg"
    Y el import del módulo válido "mymod" no se marca como alucinación

  @stdlib
  Escenario: Un import de stdlib no es alucinación
    Dado un import a "os"
    Cuando se chequea con check_python_import
    Entonces el status no es HALLUCINATION