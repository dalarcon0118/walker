"""test_golden_hit_bdd.py — F2a: retrieval hit@5 en el golden set (ADR-084).

Contrato: el golden set de 20 preguntas (paraphrases de título) se recupera en
top-5 con hit@5 >= 0.85. Si el CKG no está en el repo (CI sin datos) se salta.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CKG = ROOT / "data" / "cognitive.sqlite3"
GOLDEN = ROOT / "data" / "golden" / "queries.json"


@pytest.mark.skipif(not CKG.exists(), reason="CKG no disponible en este entorno")
@pytest.mark.skipif(not GOLDEN.exists(), reason="golden set no disponible")
def test_golden_hit_at_5_cumple_umbral():
    from returns.pipeline import is_successful

    from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import (
        CkgReadService,
    )

    queries = json.loads(GOLDEN.read_text())
    hits = 0
    for q in queries:
        result = CkgReadService.gather_context(str(ROOT), query=q["question"])
        nodes = result.value_or([]) if is_successful(result) else []
        ids = [str(n.get("id") or n.get("_id") or "") for n in nodes[:5]]
        if q["expected"] in ids:
            hits += 1
    rate = hits / len(queries)
    assert rate >= 0.85, f"hit@5 = {hits}/{len(queries)} = {rate:.2f} < 0.85"


@pytest.mark.skipif(not CKG.exists(), reason="CKG no disponible en este entorno")
@pytest.mark.skipif(not (ROOT / "data/golden/queries-natural.json").exists(), reason="golden natural no disponible")
def test_golden_natural_hit_at_5_cumple_umbral():
    """Golden set de 20 preguntas en lenguaje natural (no paraphrases de titulo).

    El umbral es el mismo (>=0.85) pero la dificultad es mayor: las preguntas
    no repiten el vocabulario del ADR. 17/20 = 0.85 pasa por un margen de una
    pregunta: los 3 misses son mismatch de vocabulario (engine/composition-root,
    intake/gate, wait-states/persistencia), no un fallo del índice.
    """
    from returns.pipeline import is_successful

    from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import (
        CkgReadService,
    )

    path = ROOT / "data" / "golden" / "queries-natural.json"
    queries = json.loads(path.read_text())
    hits = 0
    for q in queries:
        result = CkgReadService.gather_context(str(ROOT), query=q["question"])
        nodes = result.value_or([]) if is_successful(result) else []
        ids = [str(n.get("id") or n.get("_id") or "") for n in nodes[:5]]
        if q["expected"] in ids:
            hits += 1
    rate = hits / len(queries)
    assert rate >= 0.85, f"hit@5 natural = {hits}/{len(queries)} = {rate:.2f} < 0.85"
