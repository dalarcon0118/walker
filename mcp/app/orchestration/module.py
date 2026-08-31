"""app/orchestration/module.py — Manifiesto de la capa de orquestación cross-feature (ADR-066 P5).

Vive en el composition root (app/) porque orquesta features entre sí; NO es un
bounded context. Registra su presencia en el grafo de módulos para que el
composition root la descubra y valide aciclicidad (ADR-065 divergencia #4
resuelta: la 6ª capa queda documentada y declarada).
"""
from __future__ import annotations

from shared.libs.module_system.contracts import ModuleManifest


MODULE = ModuleManifest(
    name="app_orchestration",
    imports=["shared", "features", "app"],
    providers=[],
    adapters=[],
    exports=["orchestration_services"],
)

# Auto-registro por import (ADR-WALKER-068): el composition root importa este
# modulo; al importarlo se registra su manifest. Sin lista central de paths.
from shared.libs.module_system.global_registry import register_manifest as _register_manifest

_register_manifest(MODULE)
