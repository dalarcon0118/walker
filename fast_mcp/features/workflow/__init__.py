"""fast_mcp.features.workflow — Bounded context de ejecución (Fase 3).

Port autónomo del workflow engine (ADR-WALKER-071 §0 #4/#5): estado del
workflow, transiciones, tasks y side effects. Vivirá bajo `domain/` +
`application/` + `infrastructure/`; la superficie (phases/) consume objetos
cableados por `fast_mcp.composition` (regla de coherencia §3bis 2/3).

Este paquete NO importa nada de `mcp/` (ni app/, features/, shared/,
protocols/): los casos de uso viven aquí, no se llaman de forma remota.
"""