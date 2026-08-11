# language: es
# tests/features/cpg_quality_gates.feature
#
# Feature: Quality gates y dispatch de side-effects del CPG.
#
# El orchestrator dispara quality_gate_failed cuando signals.quality_passed=False
# en el side-effect update_cpg. El envelope estándar (side_effect_contract)
# normaliza ok/failed/degraded.

Característica: Quality gates y dispatch de side-effects del CPG
  Como arquitecto del módulo CPG
  Quiero que un fallo de calidad del build dispare el evento quality_gate_failed
  Para que el workflow avance hacia REFINE en vez de seguir

  @gates
  Escenario: Envelope estándar de side-effects
    Dado un envelope ok de update_cpg
    Entonces el envelope es estándar
    Y su status es ok
    Y su scope es code_index

  @gates
  Escenario: Quality gate fallido dispara quality_gate_failed
    Dado un resultado update_cpg con quality_passed en False
    Cuando el orchestrator despacha los side-effects
    Entonces se dispara quality_gate_failed
    Y el workflow tiene failed_gates con cyclomatic_complexity

  @gates
  Escenario: Quality gate aprobado no dispara eventos
    Dado un resultado update_cpg con quality_passed en True
    Cuando el orchestrator despacha los side-effects
    Entonces no se dispara ningún evento