"""features/cli_interface/module.py — Manifiesto de WorkflowApiModule (ADR-WALKER-068).

UNA responsabilidad: API surface del workflow para adaptadores + context_resolver
(adapter IContextValidator). Depende del engine (shared) + PathModule; NO
importa AppModule (INV-W068-03) ni OrchestrationModule (INV-W068-05). Fase 1:
declaración; providers en Fase 3 (move de `app/api/workflow_facade.py` +
`app/context_resolver.py`).
"""
from __future__ import annotations

from shared.libs.module_system.contracts import ModuleManifest


MODULE = ModuleManifest(
    name="features_cli_interface",
    imports=["shared", "features_common"],
    providers=[],
    adapters=[],
    exports=["workflow_facade", "context_resolver"],
    register=None,
)