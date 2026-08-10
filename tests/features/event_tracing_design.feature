# language: es
# tests/features/event_tracing_design.feature
#
# Feature: Event tracing design (ADR-WALKER-018)
#
# Como sistema de tracing
# Quiero garantizar integridad, dedupe y migración idempotente
# Para que el historial de eventos sea confiable y sin pérdida

Característica: Diseño de event tracing (ADR-WALKER-018)
  Como sistema de tracing bounded context
  Quiero garantizar round-trip simétrico, fail-closed, dedupe central y migración idempotente
  Para que el historial de eventos sea confiable y sin pérdida

  @roundtrip
  Escenario: to_dict -> from_dict preserva identidad del evento
    Dado un TraceEvent con id, sprint_id, parent_id, parent_type y data
    Cuando se convierte a dict y se restaura desde dict
    Entonces el evento restaurado mantiene id, sprint_id, parent_id y parent_type
    Y event_type y data se preservan intactos
    Y el timestamp se mantiene igual

  @search
  Escenario: search_traces es fail-closed ante filtro desconocido
    Dado una lista de eventos de tracing
    Cuando se busca con un filtro desconocido
    Entonces no se devuelven eventos (fail-closed)

  @search
  Escenario: search_traces filtra por sprint_id y sprint
    Dado eventos con sprint_id S-1 y S-2
    Cuando se filtra por sprint_id S-1
    Entonces solo el evento S-1 se devuelve
    Cuando se aplica el filtro alternativo sprint S-2
    Entonces el filtro alternativo sprint S-2 también funciona

  @dedupe
  Escenario: load_traces deduplica eventos idénticos centralmente
    Dado un índice trace canónico con 3 eventos idénticos
    Cuando se cargan los traces
    Entonces solo queda 1 evento
    Y el task_id se preserva

  @push
  Escenario: push_event persiste y load_traces lo recupera
    Dado un directorio de traces vacío
    Cuando se pushea un evento con task_id T-9
    Y se cargan los traces
    Entonces el evento T-9 se recupera correctamente
