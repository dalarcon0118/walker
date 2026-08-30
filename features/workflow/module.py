"""features/workflow/module.py — Manifiesto del bounded context `workflow` (ADR-068).

El bounded context del WORKFLOW del walker: los prompts (builders + loader +
collector) y las gates host-side (uncaptured_work) que envuelven el engine
anémico (shared/core/engine). Adaptador del puerto `IPromptCollector` del
engine delegando al collector de este feature.
"""
from __future__ import annotations

from typing import Any

from shared.core.engine.domain.ports import IPromptCollector
from shared.libs.module_system.contracts import ModuleManifest


class PromptCollectorAdapter(IPromptCollector):
    """Implementa IPromptCollector delegando al collector de features/workflow."""

    def __init__(self, prompt_collector: Any):
        self._collector = prompt_collector

    def collect(self, chain_dir: str, context: dict[str, Any]) -> dict[str, Any]:
        return self._collector(chain_dir, context)


MODULE = ModuleManifest(
    name="workflow",
    imports=["shared"],
    adapters=[(IPromptCollector, PromptCollectorAdapter)],
    exports=["prompt_collector"],
)

# Auto-registro por import (ADR-WALKER-068): el composition root importa este
# modulo; al importarlo se registra su manifest. Sin lista central de paths.
from shared.libs.module_system.global_registry import register_manifest as _register_manifest

_register_manifest(MODULE)
