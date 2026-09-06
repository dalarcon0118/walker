"""fast_mcp.features.event_tracing — TraceGraph: memoria de eventos de ejecución.

PROPÓSITO (taxonomía de memorias, project.md): memoria de la EJECUCIÓN —
escribe y lee eventos del propio MCP y del agente: herramientas usadas
(agente y MCP), mensajes user/agent, estados del workflow y tareas.

Cumplimiento (auditado 2026-09-05, canónica: 872 docs; T-14):
  ✓ herramientas del MCP: ToolInvoked (62) + ToolRegistered (806, arranque)
  ✓ eventos del workflow: CognitiveTracer emite submit/transition/error/task_execute
  ✓ mensajes user/agent: tool `trace_message` (handlers/trace.py) persiste
    event_type 'user_message'/'agent_message' con role/text/session_id en data
    —`user_message` añadido al dominio (domain/values.py, T-14)
Las brechas restantes del análisis del tracer están registradas (memoria #11916,
R-1..R-6: read-model unificado, Result en el puerto, has_kg_query, cache global).

Ported from mcp/features/event_tracing (ADR-WALKER-071).
"""
