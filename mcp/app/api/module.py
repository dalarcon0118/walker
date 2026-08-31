"""app/api/module.py — Manifiesto de WorkflowApiModule (ADR-WALKER-068).

UNA responsabilidad: API surface del workflow para los adapters (CLI/MCP):
facade submit/transition/status/prepare + gates host-side. Es capa de
APLICACIÓN del host (los controllers de protocols la inyectan por contrato);
no es un adapter (corrección Fase 3).

Fase 3: la instancia la construye el main (register_all) y se registra como
servicio "workflow"; este manifest DECLARA el módulo + su export por contrato
(self-registro por import, §2.6).
"""
from __future__ import annotations

from shared.libs.module_system.contracts import ModuleManifest
from shared.libs.module_system.global_registry import register_manifest as _register_manifest


MODULE = ModuleManifest(
    name="app_workflow_api",
    imports=["shared"],
    providers=[],
    adapters=[],
    exports=["workflow_facade"],
    register=None,
)

_register_manifest(MODULE)