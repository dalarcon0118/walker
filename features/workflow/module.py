"""features/workflow/module.py — Manifiesto del bounded context `workflow` (ADR-068).

El bounded context del WORKFLOW del walker: las gates host-side
(uncaptured_work) y los prompts que envuelven el engine anémico
(shared/core/engine).

Manifest HONESTO (Fase 5, M-03): declara las deps reales (project +
delta_tracking — consumidos vía servicios en la aplicación). El adapter
IPromptCollector se ELIMINÓ (Fase 5, M-05): el engine anémico no lo consume
(ADR-067) y el registry cablea "prompt_collector" directamente (registry.py:589).
"""
from __future__ import annotations

from shared.libs.module_system.contracts import ModuleManifest


MODULE = ModuleManifest(
    name="workflow",
    imports=["shared", "project", "delta_tracking"],
    providers=[],
    adapters=[],
    exports=[],
)

# Auto-registro por import (ADR-WALKER-068): el composition root importa este
# modulo; al importarlo se registra su manifest. Sin lista central de paths.
from shared.libs.module_system.global_registry import register_manifest as _register_manifest

_register_manifest(MODULE)