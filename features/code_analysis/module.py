"""features/code_analysis/module.py — Manifiesto del bounded context code_analysis.

Prototipo ADR-066: declara su dependencia del módulo "shared" y bindea el puerto
ICpgAnalysisPort a su implementación real WalkerCpgAnalysisAdapter vía adapters[].
"""
from shared.libs.module_system.contracts import ModuleManifest
from shared.core.engine.domain.ports import ICpgAnalysisPort
from features.code_analysis.infrastructure.adapters.walker_cpg_adapter import (
    WalkerCpgAnalysisAdapter,
)


MODULE = ModuleManifest(
    name="code_analysis",
    imports=["shared"],
    adapters=[(ICpgAnalysisPort, WalkerCpgAnalysisAdapter)],
    exports=["cpg_analysis_service"],
)

# Auto-registro por import (ADR-WALKER-068): el composition root importa este
# modulo; al importarlo se registra su manifest. Sin lista central de paths.
from shared.libs.module_system.global_registry import register_manifest as _register_manifest

_register_manifest(MODULE)

# ADR-068 Fase 2: importar el schema de la feature dispara el auto-registro
# del indice de memoria (register_memory_index).
from features.code_analysis import schema as _schema  # noqa: F401
