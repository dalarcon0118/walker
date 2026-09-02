"""domain/ports.py — Ports (interfaces) of the workflow domain.

Los puertos viven en el KERNEL como contrato neutral (shared_kernel.ports,
DIP): el workflow los consume, otros features (code_analysis, project...) los
implementan sin depender de este feature. Este módulo re-exporta el contrato
para no romper los imports existentes (regla de coherencia §3bis R1).

PRINCIPLE: the domain defines WHAT it needs, not HOW it's implemented.
Infrastructure adapters implement these ports.
"""
from __future__ import annotations

from fast_mcp.shared_kernel.ports import (
    IWorkflowStore,
    ITracer,
    ISprintService,
    IConflictResolver,
    IWorkflowIO,
    ISideEffectEngine,
    IProjectContext,
    ICpgAnalysisPort,
)

__all__ = [
    "IWorkflowStore",
    "ITracer",
    "ISprintService",
    "IConflictResolver",
    "IWorkflowIO",
    "ISideEffectEngine",
    "IProjectContext",
    "ICpgAnalysisPort",
]