# language: es
# tests/features/delta_use_cases.feature
#
# Feature: Delta use cases
#
# Como sistema de tracking
# Quiero operar sobre springs y specs mediante use cases aislados
# Para garantizar la logica de negocio sin depender de CognitiveGraph

Característica: Operaciones de use cases de delta tracking
  Como sistema de tracking
  Quiero operar sobre springs y specs mediante use cases aislados
  Para garantizar la logica de negocio sin depender de CognitiveGraph

  Antecedentes:
    Dado que existe un repositorio en memoria vacio

  @delta
  Escenario: CreateSpringUseCase deriva active desde status
    Dado un repositorio en memoria vacio
    Cuando ejecuto CreateSpringUseCase con status active sobre sprint-a
    Entonces el spring tiene active True
    Y el repositorio persiste active True
    Cuando ejecuto CreateSpringUseCase con status planning sobre sprint-b
    Entonces el spring-b tiene active False

  @delta
  Escenario: UpdateSpringUseCase set_status deriva active
    Dado un repositorio con un spring en planning
    Cuando ejecuto set_status a active
    Entonces el spring tiene status active y active True
    Cuando ejecuto set_status a closed
    Entonces el spring tiene active False

  @delta
  Escenario: UpdateSpringUseCase set_context acepta kwargs sueltos
    Dado un repositorio con un spring existente
    Cuando ejecuto set_context con dict posicional
    Entonces el context se actualiza
    Cuando ejecuto set_context con kwargs sueltos
    Entonces el context se actualiza con los kwargs

  @delta
  Escenario: UpdateSpringUseCase execute ignora inmutables y None
    Dado un repositorio con un spring existente
    Cuando ejecuto execute con goal, spring_id inmutable y display_name None
    Entonces el goal se actualiza
    Y el spring_id no cambia
    Y display_name queda vacio

  @delta
  Escenario: UpdateSpringUseCase execute sin entidad no crea
    Dado un repositorio vacio
    Cuando ejecuto execute sobre un id inexistente
    Entonces retorna None
    Cuando ejecuto set_status sobre un id inexistente
    Entonces retorna None

  @delta
  Escenario: AddSpecUseCase crea spec y edge contains
    Dado un repositorio con un spring existente
    Cuando ejecuto AddSpecUseCase con objetivo y dominio
    Entonces la spec tiene spec_id SPEC-001
    Y el repositorio persiste el objetivo
    Y se crea un edge contains

  @delta
  Escenario: UpsertSpecsUseCase crea y refina sin duplicar
    Dado un repositorio con un spring existente
    Cuando ejecuto UpsertSpecsUseCase con dos objetivos nuevos
    Entonces se crean 2 specs y 0 se refinan
    Cuando ejecuto UpsertSpecsUseCase con un objetivo existente para refinar
    Entonces se crean 0 specs y 1 se refina
    Y no hay specs duplicadas
    Y la spec refinada tiene status specified y result_state ok

  @delta
  Escenario: QueryDeltaUseCase get_current_spec prioriza in_progress
    Dado un spring con 3 specs: 1 completed, 1 in_progress, 1 draft
    Cuando ejecuto get_current_spec
    Entonces devuelve la spec in_progress

  @delta
  Escenario: QueryDeltaUseCase find_spec_by_objective es case-sensitive
    Dado un spring con una spec Fix auth
    Cuando busco por objetivo exacto
    Entonces encuentra la spec
    Cuando busco en minusculas
    Entonces no encuentra la spec
    Cuando busco por cadena vacia
    Entonces no encuentra la spec

  @delta
  Escenario: GetSprintProgressUseCase agrega stats y pct
    Dado un spring con 3 specs: 1 completed, 1 in_progress, 1 draft
    Cuando ejecuto get_sprint_progress
    Entonces total_specs es 3
    Y completed es 1
    Y in_progress es 1
    Y remaining es 2
    Y progress_pct es 33.3

  @delta
  Escenario: derive_display_name toma la primera frase del goal
    Cuando derivo display_name de un goal con dos frases
    Entonces devuelve la primera frase
    Cuando derivo display_name de un goal vacio
    Entonces devuelve el spring_id
    Cuando derivo display_name de un goal muy largo
    Entonces el resultado tiene longitud <= 60

  @delta
  Escenario: with_display_name completa docs legacy sin mutar otras
    Dado un doc spring con goal pero sin display_name
    Cuando aplico with_display_name
    Entonces el doc tiene display_name con la primera frase del goal
    Cuando aplico with_display_name a None
    Entonces devuelve None

  @delta
  Escenario: with_display_name no pisa display_name existente
    Dado un doc spring con display_name ya presente
    Cuando aplico with_display_name al doc con display_name existente
    Entonces no pisa el display_name existente

  @delta
  Escenario: spec_to_task_dict proyecta a formato legacy
    Dado una spec con archivos impactados
    Cuando proyecto a task dict
    Entonces task_id es SPEC-001
    Y spec_id es SPEC-001
    Y spec_index es 0
    Y impacted_artifacts tiene files
    Y type es feature

  @delta
  Escenario: load_spring_tasks degrada a [] con log si el core falla
    Dado un servicio que lanza RuntimeError
    Cuando ejecuto load_spring_tasks
    Entonces devuelve [] sin lanzar excepcion

  @delta
  Escenario: spring_id_from_chain_dir extrae el basename
    Cuando extraigo de /a/b/sprint-demo
    Entonces devuelve sprint-demo
    Cuando extraigo de /a/b/sprint-demo/
    Entonces devuelve sprint-demo
    Cuando extraigo de sprint-demo
    Entonces devuelve sprint-demo
