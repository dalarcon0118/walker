# language: es
# tests/features/dynamic_discovery.feature
#
# Feature: Dynamic task discovery
#
# Como health analyzer
# Quiero descubrir tasks por capacidad, no por nombres hardcodeados
# Para que el sistema sea resiliente a renombramientos de tasks

Característica: Discovery dinámico de tareas por propósito
  Como health analyzer del sistema
  Quiero descubrir tareas por su capability/purpose en el catálogo
  Para sugerir las herramientas correctas sin hardcodear nombres

  @discovery
  Escenario: El analyzer resuelve tasks por propósito, no por nombre
    Dado un catálogo con tasks de propósito ckg_populate, cpg_build y sprint_create
    Y un health report con warnings de CKG vacío, CPG sin nodos y sprint ausente
    Cuando se analiza el health report
    Entonces se sugieren commands de generate_kg, rebuild_index y plan_sprint
    Y no se sugieren tasks inexistentes como scan_files o build_cpg

  @discovery
  Escenario: No emite comandos inválidos cuando el catálogo está vacío
    Dado un catálogo de tasks vacío
    Y un health report con warnings de CKG vacío y CPG sin nodos
    Cuando se analiza el health report
    Entonces no se emite ningún comando task --task

  @discovery
  Escenario: Una task renombrada sigue siendo resuelta por su propósito
    Dado un catálogo con una task renombrada super_scan_v2 de propósito cpg_build
    Y un health report con CPG vacío
    Cuando se analiza el health report
    Entonces se sugiere el command de super_scan_v2

  @gates
  Escenario: Las edges parsean from_sub y to_sub correctamente
    Dado un workflow con edges que especifican from_sub INIT y to_sub PLANNING
    Cuando se construye el grafo del workflow
    Entonces el gate start_planning tiene source_sub_state INIT y target_sub_state PLANNING
    Y el gate start_execution tiene source_sub_state PLANNING y target_sub_state ACTIVE

  @gates
  Escenario: get_available_gates filtra por sub-estado actual
    Dado un workflow con sub-estados INIT, PLANNING, ACTIVE, REVIEW y CLOSE
    Cuando se consultan los gates disponibles desde INIT sin datos
    Entonces solo start_planning está disponible
    Y desde PLANNING sin datos no hay gates disponibles
    Y desde REVIEW sin datos no hay gates disponibles

  @gates
  Escenario: Con datos completos, los gates del sub-estado aparecen
    Dado un workflow con sub-estados que requieren goal y specifications
    Y el estado PLANNING tiene goal y specifications completos
    Cuando se consultan los gates disponibles desde PLANNING
    Entonces start_execution y edit_plan están disponibles
    Cuando se consultan los gates disponibles desde ACTIVE
    Entonces desde ACTIVE están next_task, start_review y close_sprint
    Cuando se consultan los gates disponibles desde REVIEW con decision approve
    Entonces desde REVIEW con decision están approve y rework

  @transition
  Escenario: Transition rechaza gates inválidos desde el sub-estado actual
    Dado un workflow con gate start_execution válido solo desde PLANNING
    Y el estado actual en INIT
    Cuando se busca el gate start_execution desde INIT
    Entonces no se encuentra el gate
    Cuando se busca el gate start_planning desde INIT
    Entonces el gate start_planning desde INIT sí se encuentra

  @transition
  Escenario: No se puede transitar sin completar required_fields del sub-estado
    Dado un workflow donde REVIEW requiere decision
    Y el estado REVIEW sin decision
    Cuando se verifican precondiciones para approve
    Entonces hay errores de precondiciones para REVIEW sin decision
    Cuando el estado cambia a REVIEW con decision pero fases previas vacías
    Y se re-verifican precondiciones para approve
    Entonces con decision pero fases previas vacías sigue fallando
    Cuando el estado cambia a REVIEW con todas las fases previas completas
    Y se verifican precondiciones finales para approve
    Entonces con todas las fases previas y REVIEW completas no hay errores
