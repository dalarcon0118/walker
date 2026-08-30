"""features/memories — MemoryModule (ADR-WALKER-068 Fase 1: declaración).

RESPONSABILIDAD ÚNICA: semántica de las memorias del walker (CPG/CKG/Delta/Trace)
— schemas inyectados al storage genérico `shared/libs/cognitive_graph`,
MemoryQueryService (fachada de query), formatters de contexto.

FASE 1 (declaración): el código aún vive en `app/` (memory_schemas, memories,
context_format). Este manifiesto declara el módulo objetivo + sus edges; los
providers se pueblan en Fase 2 (move).
"""