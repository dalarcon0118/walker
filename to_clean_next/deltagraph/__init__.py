"""deltagraph — SHIM de compatibilidad.

Re-exporta todo desde features.delta_tracking para mantener
backward compatibility con ~6 imports existentes.

Usa sys.modules para redirigir imports profundos como:
    from deltagraph.domain.models import TokenIndex
    from deltagraph.application.index_service import index_sprint
    from deltagraph.infrastructure.repository import load_index

Estos ahora resuelven a features.delta_tracking.*
"""
from __future__ import annotations

import sys

from features.delta_tracking import (  # noqa: F401
    DeltaGraphService,
    TokenIndex,
    IndexEntry,
    index_sprint,
    index_all,
    search_tasks,
    search_by_source,
)
from features.delta_tracking import domain  # noqa: F401
from features.delta_tracking import application  # noqa: F401
from features.delta_tracking import infrastructure  # noqa: F401

# ── Deep import forwarding via sys.modules ─────────────────────────
# Reemplaza las entradas de sys.modules para que imports profundos
# como "from deltagraph.domain.models import TokenIndex" encuentren
# el módulo real en features.delta_tracking.
_mod_map = {
    f"{__name__}.domain": domain,
    f"{__name__}.application": application,
    f"{__name__}.infrastructure": infrastructure,
}
for mod_name, real_mod in _mod_map.items():
    sys.modules[mod_name] = real_mod
