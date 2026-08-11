# language: es
# tests/features/workflow_spring.feature
#
# Feature: Workflow Spring completo
#
# Como usuario del walker
# Quiero operar el workflow de sprint completo
# Para poder planificar, ejecutar y cerrar sprints

Característica: Workflow Spring completo
  Como usuario del walker
  Quiero operar el workflow de sprint completo
  Para poder planificar, ejecutar y cerrar sprints

  Escenario: crear spring formal
    Dado un proyecto temporal con .cognitive/
    Cuando creo un spring con goal Sprint test
    Entonces el spring existe en el delta
    Y el spring.status es planning

  Escenario: agregar specs al spring
    Dado un proyecto temporal con .cognitive/
    Y un spring sprint-test en planning
    Cuando agrego spec con objective Hacer X al spring
    Entonces la spec existe en el delta
    Y la spec.status es draft

  Escenario: listar specs del spring
    Dado un proyecto temporal con .cognitive/
    Y un spring sprint-test con 2 specs
    Cuando consulto las specs del spring
    Entonces get_specs devuelve 2 items

  Escenario: avanzar estado de spec a in_progress
    Dado un proyecto temporal con .cognitive/
    Y un spring sprint-test con spec SPEC-001
    Cuando actualizo la spec a in_progress
    Entonces la spec.status es in_progress

  Escenario: cerrar spring
    Dado un proyecto temporal con .cognitive/
    Y un spring sprint-test
    Cuando cierro el spring
    Entonces el spring.status es closed

  Escenario: submit avanza el workflow
    Dado un proyecto temporal con .cognitive/
    Y un sprint activo en ACTIVE
    Cuando invoco submit con intent test y evidence ok
    Entonces el submit es exitoso

  Escenario: transition cambia de nodo
    Dado un proyecto temporal con .cognitive/
    Y un sprint activo en ACTIVE
    Cuando invoco transition con gate start_task
    Entonces la transición es exitosa
