"""tests/test_schemas_ssot_bdd.py — T-10 (M-6): PRODUCTION_INDEX_SCHEMAS SSOT único.

1. Completitud (patrón AGENTS.md): el conjunto esperado de schemas de
   producción coincide EXACTAMENTE con el SSOT — una omisión como trace o
   ckg no pasa desapercibida.
2. Meta-test grep (failure_action de T-10): el quirk de trace
   (`event_type → 'keyword'`) vive UNA sola vez (schemas.py) — la decisión
   del PO se reduce a editar 1 línea + migración, no N copias.
"""

from __future__ import annotations

from pathlib import Path

from fast_mcp.shared_kernel.cognitive_graph.schemas import PRODUCTION_INDEX_SCHEMAS

ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {"delta", "trace", "cpg", "ckg"}

# Sitios que ANTES definían copias literales del schema de producción
# (composition root, scripts, registradores runtime de features)
SITES = [
    ROOT / "fast_mcp" / "composition.py",
    ROOT / "scripts" / "migrate_canonical_v3.py",
    ROOT / "scripts" / "m0_baseline.py",
    ROOT / "fast_mcp" / "features" / "code_analysis" / "schema.py",
    ROOT / "fast_mcp" / "features" / "cognitive_knowledge"
    / "infrastructure" / "cognitive_ckg_repo.py",
]


def test_completeness():
    assert set(PRODUCTION_INDEX_SCHEMAS) == EXPECTED, (
        f"desvío de completitud: {set(PRODUCTION_INDEX_SCHEMAS) ^ EXPECTED}"
    )
    for name in EXPECTED:
        assert PRODUCTION_INDEX_SCHEMAS[name], f"schema vacío: {name}"


def test_trace_quirk_defined_only_in_ssot():
    offenders: list[str] = []
    for path in SITES:
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if '"event_type"' in line:
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "quirk trace ('event_type') definido fuera del SSOT — la decisión PO "
        "debe editar UNA línea (schemas.py), no N copias:\n"
        + "\n".join(offenders)
    )