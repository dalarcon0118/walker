"""test_session_retention_bdd.py — retención de nodos `session` (P1.5).

Sin política, cada sesión cerrada agrega un nodo `type: session` al CKG y el
conocimiento del proyecto se llena de ruido de bajo valor. La retención usa el
mecanismo que ya existe (`supersedes` → `metadata.status=superseded`), con dos
reglas: misma tarea (una sola verdad vigente) y techo por proyecto.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext
from fast_mcp.features.tasks.handlers import _di
from fast_mcp.features.tasks.handlers import knowledge


@pytest.fixture()
def project_dir(tmp_path: Path) -> str:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "project.json").write_text(
        json.dumps(
            {
                "project": {
                    "meta": {"project_id": "ret-bdd"},
                    "location": {"repo_path": str(tmp_path)},
                }
            }
        )
    )
    (tmp_path / ".cognitive" / "projects" / "ret-bdd" / "data").mkdir(parents=True)
    return str(tmp_path)


def _ctx(project_dir: str, task_id: str = "T-1") -> ExecutionContext:
    return ExecutionContext.create(chain_dir=project_dir, task_id=task_id)


def _fake_spec(monkeypatch, task_id="T-1"):
    fixed = task_id
    monkeypatch.setattr(
        _di,
        "current_spec",
        lambda chain_dir, task_id="", exact=False: {
            "objective": "Debe retener sin ruido. Sin romper los tests.",
            "files": ["a.py"],
            "task_context": {"task_id": fixed, "session_id": ""},
        },
    )
    monkeypatch.setattr(_di, "session_events", lambda chain_dir, session_id: [])


def test_captura_supersede_la_tarea_anterior(project_dir, monkeypatch):
    _fake_spec(monkeypatch)
    monkeypatch.setattr(
        _di,
        "session_facts",
        lambda chain_dir: [
            {"_id": "session-vieja-T-1", "metadata": {"task_id": "T-1",
                                                     "captured_at": "2026-11-01T00:00:00Z"}},
            {"_id": "session-otra-T-9", "metadata": {"task_id": "T-9",
                                                     "captured_at": "2026-11-02T00:00:00Z"}},
        ],
    )
    saved: dict = {}
    monkeypatch.setattr(
        _di, "save_knowledge",
        lambda chain_dir, nodes, task_id="", session_id="": saved.update(nodes=nodes) or {"ok": True},
    )

    out = knowledge.capture_knowledge(_ctx(project_dir), {"session_id": "S-1", "task_id": "T-1"})

    assert out.ok
    node = saved["nodes"][0]
    # La vieja de la MISMA tarea se supersede; la de otra tarea, no.
    assert node["supersedes"] == ["session-vieja-T-1"]
    assert out.data["superseded"] == 1


def test_techo_conserva_las_mas_recientes(project_dir, monkeypatch):
    _fake_spec(monkeypatch)
    total = knowledge.MAX_SESSION_FACTS + 5  # 30
    facts = [
        {"_id": f"session-{i}-T-{i}", "metadata": {
            "task_id": f"T-{i}",
            # i=0 es la más vieja; el techo debe superseder las 6 más viejas.
            "captured_at": f"2026-11-{i + 1:02d}T00:00:00Z",
        }}
        for i in range(total)
    ]
    monkeypatch.setattr(_di, "session_facts", lambda chain_dir: facts)
    saved: dict = {}
    monkeypatch.setattr(
        _di, "save_knowledge",
        lambda chain_dir, nodes, task_id="", session_id="": saved.update(nodes=nodes) or {"ok": True},
    )

    out = knowledge.capture_knowledge(_ctx(project_dir), {"session_id": "S-1", "task_id": "T-NEW"})

    assert out.ok
    superseded = saved["nodes"][0]["supersedes"]
    # 30 existentes, techo 25: se superseden 6 (30 - (25 - 1)).
    assert len(superseded) == total - (knowledge.MAX_SESSION_FACTS - 1)
    assert "session-0-T-0" in superseded and "session-5-T-5" in superseded
    assert "session-29-T-29" not in superseded


def test_gather_context_no_cita_superseded(monkeypatch):
    """Un nodo superseded no es autoridad vigente en el contexto (P1.5)."""
    from fast_mcp.features.cognitive_knowledge.application.services import ckg_read

    class FakeCkg:
        def search(self, q):
            return [
                {"_id": "adr-viejo", "metadata": {"status": "superseded"}},
                {"_id": "adr-vigente", "metadata": {}},
            ]

    monkeypatch.setattr(ckg_read, "_resolve_ckg", lambda chain_dir: (FakeCkg(), None))

    rows = ckg_read.CkgReadService.gather_context("/tmp/x").value_or([])

    assert [r["_id"] for r in rows] == ["adr-vigente"]


def test_la_tarea_del_plan_resuelve_la_spec(project_dir, monkeypatch):
    """El rótulo del caller no manda si el plan de la sesión dice la tarea."""
    spec = {
        "objective": "Debe capturar con la identidad de la spec.",
        "files": ["a.py"],
        "task_context": {"task_id": "T-REAL", "session_id": ""},
    }
    monkeypatch.setattr(
        _di,
        "current_spec",
        lambda chain_dir, task_id="", exact=False: (
            spec if exact and task_id == "T-REAL" else None
        ),
    )

    class _Svc:
        def get_session_plan(self, session_id):
            return {"task": "T-REAL"}

    monkeypatch.setattr(_di, "sprint_service", lambda chain_dir: _Svc())
    monkeypatch.setattr(_di, "session_events", lambda chain_dir, session_id: [])
    monkeypatch.setattr(_di, "session_facts", lambda chain_dir: [])
    saved: dict = {}
    monkeypatch.setattr(
        _di, "save_knowledge",
        lambda chain_dir, nodes, task_id="", session_id="": saved.update(
            nodes=nodes, task_id=task_id
        ) or {"ok": True},
    )

    out = knowledge.capture_knowledge(
        _ctx(project_dir), {"session_id": "S-1", "task_id": "rotulo-arbitrario"}
    )

    assert out.ok
    assert saved["nodes"][0]["id"].endswith("T-REAL")
    assert saved["nodes"][0]["metadata"]["task_id"] == "T-REAL"
    assert saved["task_id"] == "T-REAL"


def test_no_hereda_la_identidad_de_otra_spec_in_progress(project_dir, monkeypatch):
    """Un id desconocido no se convierte en la spec `in_progress` del sprint."""
    from types import SimpleNamespace

    calls: list[bool] = []

    def _current_spec(chain_dir, task_id="", exact=False):
        calls.append(exact)
        if exact:
            return None  # la tarea del caller no está en el sprint
        return {
            "objective": "otra tarea del sprint",
            "files": ["vieja.py"],
            "task_context": {"task_id": "T-VIEJA", "session_id": "S-VIEJA"},
        }

    monkeypatch.setattr(_di, "current_spec", _current_spec)
    monkeypatch.setattr(
        _di, "session_events",
        lambda chain_dir, session_id: [
            SimpleNamespace(event_type="write", files=["nuevo.py"])
        ],
    )
    monkeypatch.setattr(_di, "session_facts", lambda chain_dir: [])
    saved: dict = {}
    monkeypatch.setattr(
        _di, "save_knowledge",
        lambda chain_dir, nodes, task_id="", session_id="": saved.update(
            nodes=nodes, task_id=task_id
        ) or {"ok": True},
    )

    out = knowledge.capture_knowledge(
        _ctx(project_dir), {"session_id": "S-1", "task_id": "T-NUEVA"}
    )

    assert out.ok
    assert saved["nodes"][0]["metadata"]["task_id"] == "T-NUEVA"
    assert "vieja.py" not in saved["nodes"][0]["metadata"]["files"]
    assert "nuevo.py" in saved["nodes"][0]["metadata"]["files"]
    assert False not in calls, "nunca debe usar la heurística `in_progress`"


def test_captura_supersede_en_ckg_real(project_dir, monkeypatch):
    """End-to-end con el mecanismo existente: el viejo queda superseded."""
    from fast_mcp.composition import _build_task_registry
    from fast_mcp.features.cognitive_knowledge import ckg_service
    from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import (
        CkgReadService,
    )
    from fast_mcp.shared_kernel.project_support import ProjectService

    _build_task_registry()  # cablea _di (writer real + reader real)
    _fake_spec(monkeypatch)
    ctx = _ctx(project_dir)

    first = knowledge.capture_knowledge(ctx, {"session_id": "S-1", "task_id": "T-1"})
    second = knowledge.capture_knowledge(ctx, {"session_id": "S-2", "task_id": "T-1"})
    assert first.ok and second.ok
    assert second.data["superseded"] == 1

    active = CkgReadService.query_active(project_dir, "session")
    assert len(active) == 1, "solo una verdad vigente por tarea"
    assert "S-2" in str(active[0].get("_id"))

    data_dir = str(ProjectService.resolve_paths(project_dir).data_dir)
    # La query normal excluye superseded: para verificar la historia hay que
    # pedirlas explícitamente (el nodo no se borra, se marca).
    ckg = ckg_service(data_dir)
    all_nodes = ckg.query({"include_superseded": True, "size": 100000}) or []
    old = [n for n in all_nodes if "S-1" in str(n.get("_id") or "")]
    assert old, "el nodo viejo no se borra: se supersede (historia)"
    assert (old[0].get("metadata") or {}).get("status") == "superseded"
