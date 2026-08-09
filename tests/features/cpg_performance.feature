# language: es
# tests/features/cpg_performance.feature
#
# Feature: Rendimiento build completo vs update incremental.
#
# El update incremental (index_task_files sobre files_changed) debe ser más
# rápido que el build completo. Es el contrato de eficiencia del ADR-013.

Característica: Rendimiento del build incremental
  Como arquitecto del módulo CPG
  Quiero que actualizar un solo archivo sea más barato que construir todo
  Para que el ciclo de desarrollo sea rápido

  @rendimiento
  Escenario: Update incremental es más rápido que build completo
    Dado un builder del CPG con 100 módulos generados
    Cuando se hace un build completo y luego un update incremental
    Entonces el update indexa el archivo cambiado
    Y el update tarda menos que el build completo