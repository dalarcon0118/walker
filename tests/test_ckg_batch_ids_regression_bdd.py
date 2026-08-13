"""Tests de regresión — batch persist sin colisión de IDs (bug de pérdida silenciosa).

Bug diagnosticado en producción (frontend react-native):
  - ckg_analyze action=save con 6 nodos (2 decisions + 4 patterns) reportó
    nodes_created: 6, pero solo persistieron 2: todos los nodos del MISMO
    tipo colisionaban en el mismo doc_id porque next_id() consulta max_key
    contra el índice PERSISTIDO (que no incluye el batch actual, el store es
    al final) → new_docs[doc_id] sobrescribía silenciosamente al anterior.

Regresión: un batch de N nodos del mismo tipo debe producir N IDs únicos y
persistir N docs en disco.
"""
from __future__ import annotations

import gzip
import json

from tests.conftest import make_walker_project


def _load_ckg_docs(project_root: str) -> list[dict]:
    """Lee el storage ckg.json.gz del proyecto y devuelve los docs."""
    import os

    path = os.path.join(project_root, "data", "ckg.json.gz")
    with gzip.open(path, "rt") as f:
        payload = json.load(f)
    return list(payload.get("docs", []))


def test_batch_same_type_genera_ids_unicos_y_persiste_todos(tmp_path):
    """6 nodos (2 decisions + 4 patterns) en UN batch → 6 IDs únicos en disco."""
    project_root = make_walker_project(tmp_path)
    from features.cognitive_knowledge.infrastructure.cognitive_ckg_repo import (
        CognitiveCkgRepository,
    )

    repo = CognitiveCkgRepository(project_root)
    nodes = [
        {"label": "decision:expo-sdk-54", "type": "decision",
         "metadata": {"topic": "expo"}},
        {"label": "decision:auth-tests", "type": "decision",
         "metadata": {"topic": "auth"}},
        {"label": "pattern:cache-validator", "type": "pattern",
         "metadata": {"topic": "cache"}},
        {"label": "pattern:bet-win-win", "type": "pattern",
         "metadata": {"topic": "domain"}},
        {"label": "pattern:reveal-rewards", "type": "pattern",
         "metadata": {"topic": "rewards"}},
        {"label": "pattern:repositories-offline", "type": "pattern",
         "metadata": {"topic": "repos"}},
    ]
    result = repo.save_nodes(nodes)

    assert result["ok"] is True, f"save_nodes no ok: {result}"
    assert result["nodes_created"] == 6, (
        f"nodes_created debe ser 6 (persistidos reales), fue {result['nodes_created']}"
    )
    ids = result["node_ids"]
    assert len(ids) == len(set(ids)) == 6, f"IDs colisionando: {ids}"

    docs = _load_ckg_docs(project_root)
    assert len(docs) == 6, f"En disco deben persistir 6 docs, hay {len(docs)}"
    labels = {d.get("statement") for d in docs}
    assert {"decision:expo-sdk-54", "decision:auth-tests",
            "pattern:cache-validator", "pattern:bet-win-win",
            "pattern:reveal-rewards", "pattern:repositories-offline"} <= labels, (
        f"Faltan nodos en disco: {sorted(labels)}"
    )


def test_batch_tras_batch_no_colisiona(tmp_path):
    """Dos batches consecutivos del mismo tipo continúan la numeración sin pisarse."""
    project_root = make_walker_project(tmp_path)
    from features.cognitive_knowledge.infrastructure.cognitive_ckg_repo import (
        CognitiveCkgRepository,
    )

    repo = CognitiveCkgRepository(project_root)
    r1 = repo.save_nodes([
        {"label": "decision:primera", "type": "decision"},
        {"label": "decision:segunda", "type": "decision"},
    ])
    r2 = repo.save_nodes([
        {"label": "decision:tercera", "type": "decision"},
    ])

    ids = [*r1["node_ids"], *r2["node_ids"]]
    assert len(ids) == len(set(ids)) == 3, f"IDs colisionando entre batches: {ids}"

    docs = _load_ckg_docs(project_root)
    assert len(docs) == 3