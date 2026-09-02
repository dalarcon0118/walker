"""fast_mcp.memories — Capa de adaptadores de las tools de memoria (Fase 2).

NO es un bounded context del dominio: es la capa de despacho/adaptación de la
superficie (runners de composition) hacia los servicios de los features
(code_analysis, cognitive_knowledge). Por eso vive FUERA de `features/`
(regla de coherencia §3bis R1: los features no se importan entre sí; los
adaptadores de integración viven en esta capa y se cablean en composition.py).

Cada handler recibe un MemoryContext resuelto y devuelve un dict.

Usage:
    from fast_mcp.memories.handlers import dispatch, HANDLER_NAMES

    result = dispatch("read_code", ctx, file="src/main.py")
"""
