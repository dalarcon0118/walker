"""features/orchestration/module.py — Manifiesto de OrchestrationModule (ADR-WALKER-068).

UNA responsabilidad: use cases de aplicación cross-context (G10). Depende de
features + shared + MemoryModule; NO importa AppModule ni WorkflowApiModule
(INV-W068-05). Fase 1: declaración de boundaries; providers en Fase 3 (move de
`app/orchestration/*_service.py` + `contracts.py`).
"""
from __future__ import annotations

from shared.libs.module_system.contracts import ModuleManifest


MODULE = ModuleManifest(
    name="features_orchestration",
    imports=["shared", "features", "features_memories"],
    providers=[],
    adapters=[],
    exports=["orchestration_services"],
    register=None,
)