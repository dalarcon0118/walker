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
