"""fast_mcp.features.delta_tracking — DeltaGraph: memoria de planes (delta como SSOT).

PROPÓSITO (taxonomía de memorias, project.md): memoria de PLANES, TAREAS,
ESPECIFICACIONES y ESTADO del proyecto; SSOT del sprint/specs (INV-004,
ADR-WALKER-013/016). El workflow guarda y lee aquí.

Domain:     Spring, Spec (pure entities)
Application: SprintService (thin orchestrator), ports
Infrastructure: CognitiveDeltaRepository (Sprint 5.2)

R1: this feature only imports itself, shared_kernel, stdlib.
"""
