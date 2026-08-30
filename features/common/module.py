"""features/common/module.py — Manifiesto de PathModule (ADR-WALKER-068).

UNA responsabilidad: resolver paths (SSOT, INV-W068-06). Depende solo de
config (shared). Fase 1: declaración; provider en Fase 4 (move de
`app/path_resolver.py`).
"""
from __future__ import annotations

from shared.libs.module_system.contracts import ModuleManifest


MODULE = ModuleManifest(
    name="features_common",
    imports=["shared"],
    providers=[],
    adapters=[],
    exports=["path_resolver"],
    register=None,
)