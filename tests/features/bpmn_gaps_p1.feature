Feature: BPMN P1 gaps
  Como arquitecto del workflow engine
  Quiero que el motor maneje eventos de señal, compensaciones y bloqueos
  Para que el workflow sea resiliente y pueda recuperarse de fallos

  Scenario: El workflow no se queda bloqueado indefinidamente cuando una tarea no puede continuar
    Given un sprint bloqueado esperando una consulta al knowledge graph
    When se aplica la estrategia de resolución "saltar"
    Then el sprint continúa automáticamente sin intervención humana
    And el bloqueo se registra como resuelto

  Scenario: El workflow fuerza el avance cuando una tarea está atascada
    Given un sprint bloqueado en una sub-tarea
    When se aplica la estrategia de resolución "forzar"
    Then el sprint avanza al siguiente paso sin quedar bloqueado
    And se preserva el contexto del workflow

  Scenario: El workflow espera pacientemente cuando la resolución lo indica
    Given un sprint bloqueado esperando una señal externa
    When se aplica la estrategia de resolución "esperar"
    Then el sprint mantiene el bloqueo activo
    And no se pierde el estado de espera

  Scenario: El workflow escala a un nodo de emergencia cuando una tarea falla
    Given un sprint bloqueado que no se puede resolver automáticamente
    When se aplica la estrategia de resolución "escalar"
    Then el sprint transiciona al nodo de escalación designado
    And se notifica la intervención requerida

  Scenario: El workflow revierte cambios cuando una tarea requiere compensación
    Given un sprint con cambios aplicados que requiere rollback
    When se aplica la estrategia de resolución "compensar"
    Then los cambios se revierten en orden inverso
    And el sprint queda en un estado consistente

  Scenario: El workflow se recupera automáticamente después de un timeout
    Given un sprint esperando un evento que tarda demasiado
    When el timeout se cumple
    Then el sprint se desbloquea automáticamente
    And se registra la resolución por timeout

  Scenario: El workflow reacciona a señales de negocio sin polling
    Given un sprint esperando una señal de aprobación
    When llega la señal "aprobación_recibida"
    Then el sprint se desbloquea automáticamente
    And continúa con el flujo definido

  Scenario: Las señales llegan a todos los suscriptores relevantes
    Given múltiples nodos suscritos a una señal de actualización
    When se emite la señal
    Then todos los nodos suscritos reciben la notificación
    And se registra el historial de señales emitidas

  Scenario: Las señales globales se propagan a todo el workflow
    Given un nodo suscrito a señales globales
    When se emite una señal global
    Then el nodo recibe la notificación
    And el flujo puede reaccionar coordinadamente

  Scenario: Las compensaciones se ejecutan en orden inverso al de aplicación
    Given un sprint con múltiples efectos secundarios aplicados
    When se ejecuta la compensación
    Then los efectos se revierten en orden inverso
    And no se dejan efectos huérfanos

  Scenario: El sprint se desbloquea por señal sin intervención humana
    Given un sprint en estado "waiting for signal"
    When se recibe la señal esperada
    Then el sprint transiciona automáticamente
    And se registra la causa de desbloqueo

  Scenario: El sprint se desbloquea por timeout sin intervención humana
    Given un sprint en espera con timeout configurado
    When el tiempo de espera se agota
    Then el sprint transiciona automáticamente
    And se registra la causa de desbloqueo
