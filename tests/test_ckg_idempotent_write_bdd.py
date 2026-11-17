"""test_ckg_idempotent_write_bdd.py — ids determinísticos en el CKG.

Problema medido (2026-11-17): `save_nodes` SIEMPRE generaba id autoincremental
(`next_id(type)`), así que re-correr una ingesta duplicaba nodos. Medido con la
ingesta de ADRs de walker: 42 ADRs → 42 nodos; segunda corrida → 84 nodos, con
el CKG sirviendo dos copias de cada decisión y el FTS pagando el doble.

El fix: un `id`/`_id` explícito en el payload se usa como clave (upsert). Los
payloads sin id siguen con la numeración por tipo, sin cambios.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fast_mcp.features.cognitive_knowledge.application.services.ckg_write import (
    CkgWriteService,
)
from fast_mcp.features.cognitive_knowledge.infrastructure.cognitive_ckg_repo import (
    CognitiveCkgRepository,
)


@pytest.fixture()
def chain_dir(tmp_path: Path) -> str:
    """Repo mínimo con layout cognitivo (project.json + .cognitive/projects)."""
    (tmp_path / ".cognitive" / "projects" / "demo" / "data").mkdir(parents=True)
    (tmp_path / "project.json").write_text(
        json.dumps({"project_id": "demo", "repo_path": str(tmp_path)})
    )
    return str(tmp_path)


def _repo(chain_dir: str) -> CognitiveCkgRepository:
    from fast_mcp.shared_kernel.project_support import ProjectService

    paths = ProjectService.resolve_paths(chain_dir)
    return CognitiveCkgRepository(str(paths.data_dir))


def _save(chain_dir: str, nodes: list[dict]) -> dict:
    from returns.pipeline import is_successful

    result = CkgWriteService.save_nodes(chain_dir, nodes, task_id="test")
    assert is_successful(result), result
    return result.value_or({})


def test_id_explicito_no_duplica_en_la_segunda_corrida(chain_dir):
    node = {
        "id": "adr-walker-016",
        "label": "ADR-WALKER-016: bounded context",
        "description": "primera versión",
        "type": "design-decision",
        "tags": ["architecture"],
    }
    first = _save(chain_dir, [dict(node)])
    assert first["nodes_saved"] == 1

    second = _save(chain_dir, [{**node, "description": "segunda versión"}])
    assert second["nodes_saved"] == 1

    docs = _repo(chain_dir).query({})
    assert [d["_id"] for d in docs] == ["adr-walker-016"]
    assert docs[0]["rationale"] == "segunda versión", "el upsert debe actualizar"


def test_sin_id_sigue_autoincremental(chain_dir):
    """El id determinístico es opt-in: no cambia el comportamiento existente."""
    _save(chain_dir, [{"label": "una decisión", "type": "decision"}])
    _save(chain_dir, [{"label": "otra decisión", "type": "decision"}])

    ids = sorted(d["_id"] for d in _repo(chain_dir).query({}))
    assert ids == ["decision-001", "decision-002"]


def test_id_explicito_no_se_filtra_a_metadata(chain_dir):
    """`id`/`_id` son clave del repo, no un campo del fact."""
    _save(chain_dir, [{"id": "adr-walker-042", "label": "ADR 042", "type": "design-decision"}])
    doc = _repo(chain_dir).query({})[0]
    metadata = doc.get("metadata") or {}
    assert "id" not in metadata and "_id" not in metadata
