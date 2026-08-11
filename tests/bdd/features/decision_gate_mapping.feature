# language: es
# tests/features/decision_gate_mapping.feature
#
# Feature: Decision gate mapping
#
# Como usuario del workflow engine
# Quiero que las gates de decisión se resuelvan automáticamente según la intención
# Para que el flujo avance sin intervención manual cuando las condiciones se cumplen

Característica: Mapeo de decision gates en el workflow
  Como arquitecto del workflow engine
  Quiero que cada decision del usuario mapee a la gate correcta del workflow
  Para que el sprint avance automáticamente sin intervención humana

  @contrato
  Escenario: DESIGN_REVIEW approve mapea a design_approved
    Dado un workflow con DESIGN y DESIGN_REVIEW
    Y el workflow tiene edge design_approved de DESIGN a ANALYZE
    Y el workflow tiene edge design_needs_refine de DESIGN a REFINE
    Cuando el usuario expresa approve
    Entonces la gate resuelta es design_approved
    Y el workflow puede transicionar de DESIGN a ANALYZE

  @contrato
  Escenario: TASK_REVIEW approve mapea a next_task
    Dado un workflow con SPRINT y TASK_REVIEW
    Y el workflow tiene edge next_task de SPRINT a SPRINT
    Y el workflow tiene edge all_done de SPRINT a SPRINT
    Cuando el usuario expresa approve
    Entonces la gate resuelta es next_task
    Y el workflow puede continuar al siguiente task

  @contrato
  Escenario: VERIFY QA approve mapea a task_complete
    Dado un workflow con VERIFY y QA
    Y el workflow tiene edge task_complete de VERIFY a SPRINT
    Y el workflow tiene edge needs_refine de VERIFY a REFINE
    Cuando el usuario expresa approve
    Entonces la gate resuelta es task_complete
    Y el workflow puede finalizar la verificación

  @contrato
  Escenario: VERIFY PO_ACCEPT approve mapea a task_complete
    Dado un workflow con VERIFY y PO_ACCEPT
    Y el workflow tiene edge task_complete de VERIFY a SPRINT
    Cuando el usuario expresa approve
    Entonces la gate resuelta es task_complete
    Y el producto puede marcarse como completado

  @contrato
  Escenario: Sub-estado guarded resuelve la gate por decision
    Dado un workflow con DESIGN y DESIGN_REVIEW
    Y el workflow tiene edge design_approved con pre ckg_has_decision(approve)
    Cuando el workflow clasifica el sub-estado como guarded
    Y el usuario expresa approve
    Entonces la gate resuelta es design_approved
    Entonces resolve_xor_by_intent encuentra la gate

