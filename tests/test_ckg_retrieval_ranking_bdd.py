"""test_ckg_retrieval_ranking_bdd.py — autoridad y payload del CKG (F2).

Dos reglas que el golden set expuso:

1. **Autoridad primero.** Un nodo de memoria de trabajo (`source: session:*`)
   rankeaba por encima de un ADR: `decision-001` ("proceed") era top-1 de
   "cómo se separan las tareas del workflow de las acciones". El orden bm25 se
   conserva dentro de cada tier (sorted es estable).
2. **El payload no crece con el corpus.** Desde F2 se indexa el cuerpo del ADR
   (8 KB) pero se devuelven 600 chars: 20 resultados × 8 KB no entran en el
   contexto del agente.
"""

from __future__ import annotations

import fast_mcp.features.cognitive_knowledge.application.services.ckg_read as ckg_read
from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import (
    CkgReadService,
    _is_authority,
    _normalize_node,
)


def _adr() -> dict:
    return {
        "_id": "adr-walker-067-engine-pure-architecture",
        "statement": "Motor de workflow puro",
        "rationale": "cuerpo",
        "metadata": {"adr": "ADR-WALKER-067", "source": "docs/ADR-WALKER-067.md"},
    }


def _sesion() -> dict:
    return {
        "_id": "decision-001",
        "statement": "proceed",
        "rationale": "proceed",
        "metadata": {"source": "session:roadmap-session-1"},
    }


def test_autoridad_es_adr_o_docs():
    assert _is_authority(_adr())
    assert _is_authority({"id": "adr-083"})
    assert _is_authority({"metadata": {"source": "docs/ADR-X.md"}})
    assert not _is_authority(_sesion())


def test_normalize_recorta_rationale_y_declara_el_tamano():
    node = _normalize_node({"rationale": "x" * 900})
    assert node["rationale"].endswith("…")
    assert len(node["rationale"]) == 601
    assert node["rationale_chars"] == 900


def test_normalize_no_toca_rationale_corto():
    node = _normalize_node({"rationale": "corto"})
    assert node["rationale"] == "corto"
    assert "rationale_chars" not in node


class _CkgFalso:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def search(self, query: str, max_results: int = 20) -> list[dict]:
        return list(self.rows)


def test_gather_context_pone_la_autoridad_primero(monkeypatch):
    rows = [_sesion(), _adr()]
    monkeypatch.setattr(ckg_read, "_resolve_ckg", lambda _dir: (_CkgFalso(rows), None))

    result = CkgReadService.gather_context("/tmp/proyecto")
    assert result.value_or([])[0]["_id"].startswith("adr-")


def test_gather_context_no_borra_todo_si_solo_hay_sesion(monkeypatch):
    """Si no hay ningún ADR, la memoria de sesión sigue siendo la respuesta."""
    monkeypatch.setattr(
        ckg_read, "_resolve_ckg", lambda _dir: (_CkgFalso([_sesion()]), None)
    )

    result = CkgReadService.gather_context("/tmp/proyecto")
    assert [n["_id"] for n in result.value_or([])] == ["decision-001"]
