"""test_sprint_depends_bdd.py — el plan puede declarar dependencias entre specs.

`Spec.depends_on` existía y el workflow ya lo usaba para bloquear tareas, pero
`sprint_planning` no lo escribía: el agente veía todas las specs como
independientes y podía arrancar una tarea que dependía de otra.
"""

from __future__ import annotations

from fast_mcp.features.delta_tracking.application.services.sprint_service import (
    SprintService,
)
from fast_mcp.features.delta_tracking.handlers.sprint import handle_sprint_planning
from fast_mcp.features.delta_tracking.infrastructure.cognitive_delta_repo import (
    CognitiveDeltaRepository,
)
from fast_mcp.shared_kernel.cognitive_graph import build_cognitive_graph


def _service(tmp_path) -> SprintService:
    graph = build_cognitive_graph(str(tmp_path))
    graph.register_index("delta", {
        "fields": {"task": "spec_id", "spec": "spec_id", "spring": "spring_id",
                   "domain": "domain", "type": "type"},
        "types": {"spec": {}, "spring": {}},
        "description": "d",
        "search": {"type": "fulltext", "fields": ["objective", "description"]},
    })
    return SprintService(project_root=str(tmp_path), repo=CognitiveDeltaRepository(graph))


def test_sprint_planning_persiste_depends_on(tmp_path):
    svc = _service(tmp_path)

    result = handle_sprint_planning({
        "project_id": "walker",
        "spring_id": "SPR-DEP",
        "goal": "plan con dependencias",
        "status": "planning",
        "_sprint_service": svc,
        "tasks": [
            {"objective": "base", "files": ["a.py"]},
            {"objective": "depende de base", "files": ["b.py"],
             "depends_on": ["SPEC-001"]},
        ],
    })

    assert result["ok"] is True
    assert len(result["specs"]) == 2
    assert result["specs"][0]["depends_on"] == []
    assert result["specs"][1]["depends_on"] == ["SPEC-001"]
    persisted = svc.get_spec("SPR-DEP", "SPEC-002")
    assert persisted["depends_on"] == ["SPEC-001"]


def test_sprint_planning_sin_depends_on_queda_vacio(tmp_path):
    svc = _service(tmp_path)

    result = handle_sprint_planning({
        "project_id": "walker",
        "spring_id": "SPR-NODEP",
        "goal": "sin dependencias",
        "status": "planning",
        "_sprint_service": svc,
        "tasks": [{"objective": "sola"}],
    })

    assert result["specs"][0]["depends_on"] == []
