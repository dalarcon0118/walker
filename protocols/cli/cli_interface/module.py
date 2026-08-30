"""protocols/cli/cli_interface/module.py — Manifiesto del adaptador CLI (ADR-066 P5).

Input adapter de nivel protocolo basado en handlers. Declara su presencia en
el grafo de módulos del composition root (descubrimiento + aciclicidad).
"""
from __future__ import annotations

from shared.libs.module_system.contracts import ModuleManifest


MODULE = ModuleManifest(
    name="protocols_cli",
    imports=["shared", "features", "app"],
    providers=[],
    adapters=[],
    exports=["cli_interface"],
)

# Auto-registro por import (ADR-WALKER-068): el composition root importa este
# modulo; al importarlo se registra su manifest. Sin lista central de paths.
from shared.libs.module_system.global_registry import register_manifest as _register_manifest

_register_manifest(MODULE)
