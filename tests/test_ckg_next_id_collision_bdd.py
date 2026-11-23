"""tests/test_ckg_next_id_collision_bdd.py — `next_id` nunca reutiliza la clave máxima.

Bug medido 2026-11-22: `next_id(prefix)` calculaba `max_num + offset` con
`offset=0` por defecto, así que devolvía el id MÁXIMO EXISTENTE. Un
`next_id("task-ledger")` suelto sobre `task-ledger-003` devolvía
`task-ledger-003` y el `store` posterior sobreescribía el nodo.

El batch (`save_nodes`) siempre pasaba `offset=1,2,...`, así que el caso roto
era el uso suelto — el que usa cualquier migración o reparación puntual.

Contrato:
  - Índice vacío → `prefix-001`.
  - Con `prefix-003` → `prefix-004` (nunca `003`).
  - Batch: offsets 1..n dan ids consecutivos sin colisión.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fast_mcp.features.cognitive_knowledge.infrastructure.cognitive_ckg_repo import (
    CognitiveCkgRepository,
)


@pytest.fixture()
def data_dir(tmp_path: Path) -> str:
    d = tmp_path / ".cognitive" / "projects" / "demo" / "data"
    d.mkdir(parents=True)
    (d / "project.json").write_text(json.dumps({"project_id": "demo"}))
    return str(d)


def _repo(data_dir: str) -> CognitiveCkgRepository:
    return CognitiveCkgRepository(data_dir)


def test_indice_vacio_empieza_en_uno(data_dir):
    assert _repo(data_dir).next_id("decision") == "decision-001"


def test_next_id_no_devuelve_la_clave_maxima(data_dir):
    repo = _repo(data_dir)
    repo._cg.store("ckg", "task-ledger-003", {"statement": "x"})

    assert repo.next_id("task-ledger") == "task-ledger-004"


def test_batch_sigue_siendo_consecutivo(data_dir):
    repo = _repo(data_dir)
    repo._cg.store("ckg", "finding-005", {"statement": "x"})

    assert [repo.next_id("finding", offset=i) for i in (1, 2, 3)] == [
        "finding-006", "finding-007", "finding-008",
    ]


def test_guardar_el_id_devuelto_no_pisa_el_ultimo(data_dir):
    repo = _repo(data_dir)
    repo._cg.store("ckg", "task-ledger-003", {"statement": "original"})

    new_id = repo.next_id("task-ledger")
    repo._cg.store("ckg", new_id, {"statement": "migrado"})

    assert new_id == "task-ledger-004"
    assert repo._cg.get("ckg", "task-ledger-003")["_source"]["statement"] == "original"
    assert repo._cg.get("ckg", "task-ledger-004")["_source"]["statement"] == "migrado"
