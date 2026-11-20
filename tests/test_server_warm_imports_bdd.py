"""test_server_warm_imports_bdd.py — el boot pre-importa los módulos calientes.

Medido 2026-11-20: la primera ráfaga concurrente (`status` + `agent_context` +
`workflow_state`) tiró `deadlock detected by _ModuleLock('…check_cpg_health')`.
El lock era del importador, no del dominio. `build_server()` importa esos
módulos antes de aceptar requests, así que la carrera no puede ocurrir.
"""

from __future__ import annotations

import sys

from fast_mcp.server import build_server


def test_build_server_precalienta_los_imports_calientes():
    build_server()

    for module in (
        "fast_mcp.features.code_analysis.application.use_cases.check_cpg_health",
        "fast_mcp.features.cognitive_knowledge.application.services.ckg_read",
        "fast_mcp.features.tasks.infrastructure.verify_engine",
    ):
        assert module in sys.modules, f"{module} debería estar importado en el boot"
