"""test_golden_retrieval_bdd.py — el golden set corre en la suite (F2 / M-06).

Antes era un script manual (`scripts/golden_queries.py`): una regresión de
ranking no bloqueaba un merge. Este test corre las 20 consultas contra el CKG
del proyecto en proceso y falla si el hit@5 baja del piso.

Piso: 17/20 (85%). El objetivo del documento es 18/20 (90%); el piso deja un
hit de margen porque el corpus crece con cada sesión y bm25 se mueve. Se
declara el número medido en el mensaje para que la degradación sea visible.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data" / "golden" / "agent_queries.json"
FLOOR = 17


def _blob(node: dict) -> str:
    return json.dumps(node, ensure_ascii=False).lower()


@pytest.fixture(scope="module")
def golden() -> list[dict]:
    if not GOLDEN.is_file():
        pytest.skip("no hay golden set congelado")
    return json.loads(GOLDEN.read_text(encoding="utf-8")).get("queries", [])


@pytest.fixture(scope="module")
def ckg_ready() -> str:
    from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import (
        CkgReadService,
    )

    project_root = str(ROOT)
    if not CkgReadService.query_all(project_root):
        pytest.skip("el proyecto no tiene CKG indexado (CI sin store)")
    return project_root


def test_golden_hit_at_5_no_baja_del_piso(golden, ckg_ready):
    from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import (
        CkgReadService,
    )

    misses: list[str] = []
    for entry in golden:
        result = CkgReadService.gather_context(ckg_ready, query=str(entry["query"]))
        nodes = result.value_or([])[:5]
        expected = [str(x).lower() for x in entry.get("expected", [])]
        if not any(any(item in _blob(node) for item in expected) for node in nodes):
            misses.append(f"{entry['id']}: {entry['query']}")

    hits = len(golden) - len(misses)
    detail = "; ".join(misses)
    assert hits >= FLOOR, f"golden {hits}/{len(golden)} (< {FLOOR}): {detail}"
