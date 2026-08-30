"""features/main/prompts/module.py — Manifiesto del subsistema `prompts` (ADR-066 D4).

Adaptador del puerto `IPromptCollector` del engine. Vive junto al collector de
`features/main/prompts` (que lo implementa), no en `app/engine_ports.py`.
"""
from __future__ import annotations

from typing import Any

from shared.core.engine.domain.ports import IPromptCollector
from shared.libs.module_system.contracts import ModuleManifest


class PromptCollectorAdapter(IPromptCollector):
    """Implementa IPromptCollector delegando al collector de features/main."""

    def __init__(self, prompt_collector: Any):
        self._collector = prompt_collector

    def collect(self, chain_dir: str, context: dict[str, Any]) -> dict[str, Any]:
        return self._collector(chain_dir, context)


MODULE = ModuleManifest(
    name="prompts",
    imports=["shared"],
    adapters=[(IPromptCollector, PromptCollectorAdapter)],
    exports=["prompt_collector"],
)

# Auto-registro por import (ADR-WALKER-068): el composition root importa este
# modulo; al importarlo se registra su manifest. Sin lista central de paths.
from shared.libs.module_system.global_registry import register_manifest as _register_manifest

_register_manifest(MODULE)
