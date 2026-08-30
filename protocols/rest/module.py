"""protocols/rest/module.py — Manifiesto del adaptador REST (ADR-066 P5).

Input adapter de nivel protocolo: traduce HTTP a dispatch del Router. No
implementa puertos del motor; declara su presencia en el grafo de módulos
para que el composition root la descubra y valide aciclicidad.
"""
from __future__ import annotations

from protocols.rest.front_controller import FrontController
from shared.libs.module_system.contracts import ModuleManifest


MODULE = ModuleManifest(
    name="protocols_rest",
    imports=["shared", "features", "app"],
    providers=[FrontController],
    adapters=[],
    exports=["front_controller"],
)

# Auto-registro por import (ADR-WALKER-068): el composition root importa este
# modulo; al importarlo se registra su manifest. Sin lista central de paths.
from shared.libs.module_system.global_registry import register_manifest as _register_manifest

_register_manifest(MODULE)
