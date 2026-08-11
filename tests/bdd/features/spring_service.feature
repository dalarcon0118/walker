# language: es
# tests/features/spring_service.feature
#
# Feature: Spring Service — módulo features/spring
#
# Como usuario del walker
# Quiero que el spring service gestione specs y estados
# Para poder planificar y cerrar sprints

Característica: Spring Service — módulo features/spring
  Como usuario del walker
  Quiero que el spring service gestione specs y estados
  Para poder planificar y cerrar sprints

  Escenario: create_spring crea un nuevo spring
    Dado un proyecto temporal con .cognitive/
    Cuando creo un spring con goal Sprint test
    Entonces el spring existe en el delta
    Y el spring.status es planning

  Escenario: add_spec agrega una spec
    Dado un proyecto temporal con .cognitive/
    Cuando creo un spring con goal Sprint test
    Y agrego spec con objective Hacer X al spring
    Entonces la spec existe en el delta
    Y la spec.status es draft

  Escenario: get_specs lista las specs
    Dado un proyecto temporal con .cognitive/
    Cuando creo un spring con goal Sprint test
    Y el spring tiene 2 specs
    Entonces get_specs devuelve 2 items

  Escenario: update_spec_status cambia el estado
    Dado un proyecto temporal con .cognitive/
    Cuando creo un spring con goal Sprint test
    Y agrego spec con objective Hacer X al spring
    Y actualizo la spec a in_progress
    Entonces la spec existe en el delta
    Y la spec.status es in_progress

  Escenario: close_spring cierra el sprint
    Dado un proyecto temporal con .cognitive/
    Cuando creo un spring con goal Sprint test
    Y cierro el spring
    Entonces el spring.status es closed

  Escenario: ensure_spring_imported no falla si no existe
    Dado un proyecto temporal con .cognitive/
    Cuando importo un spring que no existe
    Entonces el resultado es None
