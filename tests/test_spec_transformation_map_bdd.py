"""tests/test_spec_transformation_map_bdd.py — Spec transformation_map SSOT.

SPEC-001: la entidad Spec expone transformation_map como campo estructurado
con nodes_visited y states. Este test valida la estructura base.
"""
from __future__ import annotations

import pytest

from features.delta_tracking.domain.entities import Spec


def test_spec_entity_tiene_transformation_map():
    spec = Spec(
        spring_id="spring-2026-08-10",
        objective="Test",
        transformation_map={
            "nodes_visited": ["SPRINT_PLANNING.TASK_SPEC"],
            "states": [],
            "current_status": "specified",
        },
    )
    assert "nodes_visited" in spec.transformation_map
    assert isinstance(spec.transformation_map["nodes_visited"], list)


def test_spec_transformation_map_roundtrip():
    data = {
        "nodes_visited": ["A", "B"],
        "states": [{"node": "A", "status": "done"}],
        "current_status": "done",
    }
    spec = Spec(
        spring_id="spring-2026-08-10",
        objective="Test",
        transformation_map=data,
    )
    assert spec.to_dict()["transformation_map"]["nodes_visited"] == ["A", "B"]
    assert spec.to_dict()["transformation_map"]["current_status"] == "done"
