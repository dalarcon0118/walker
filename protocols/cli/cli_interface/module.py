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
