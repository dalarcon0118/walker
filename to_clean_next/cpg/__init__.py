"""CPG — SHIM de compatibilidad.

Re-exporta todo desde features.code_analysis para mantener
backward compatibility con ~25 imports existentes.

Usa sys.modules para redirigir imports profundos como:
    from cpg.domain.graph import Node
    from cpg.application.query_service import QueryGraphUseCase
    from cpg.infrastructure.repository import CpgFileRepository

Estos ahora resuelven a features.code_analysis.*
"""
from __future__ import annotations

import sys

from features.code_analysis import (  # noqa: F401
    CPGBuilder,
    CpgQueryService,
)
from features.code_analysis import domain  # noqa: F401
from features.code_analysis import application  # noqa: F401
from features.code_analysis import infrastructure  # noqa: F401

# ── Deep import forwarding via sys.modules ─────────────────────────
# Reemplaza las entradas de sys.modules para que imports profundos
# como "from cpg.domain.graph import Node" encuentren el módulo real
# en features.code_analysis, no los archivos legacy en scripts/cpg/.
_mod_map = {
    f"{__name__}.domain": domain,
    f"{__name__}.application": application,
    f"{__name__}.infrastructure": infrastructure,
}
for mod_name, real_mod in _mod_map.items():
    sys.modules[mod_name] = real_mod
