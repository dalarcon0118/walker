"""BDD: acciones DESIGN/REVIEW que el grafo declaraba sin handler.

`gather_context` y `documenting_knowledge` (DESIGN) y `review_code`
(IMPLEMENT.CODE_REVIEW_SCAN). Sin ellas el motor no entra a DESIGN.
"""
from __future__ import annotations

from types import SimpleNamespace

from fast_mcp.features.tasks.domain.task_registry import TaskRegistry
from fast_mcp.features.tasks.handlers import design, review


def _ctx(tmp_path, task_id: str = "SPEC-002"):
    return SimpleNamespace(chain_dir=str(tmp_path), task_id=task_id, session_id="s1")


def test_registra_las_tres_acciones():
    registry = TaskRegistry()

    design.register(registry)
    review.register(registry)

    assert registry.has("gather_context")
    assert registry.has("documenting_knowledge")
    assert registry.has("review_code")


def test_gather_context_es_fail_open(tmp_path):
    out = design.gather_context(_ctx(tmp_path), {})

    assert out.ok is True
    for key in ("code_context", "knowledge_context", "spring_context", "warnings"):
        assert key in out.data


def test_documenting_knowledge_persiste(tmp_path, monkeypatch):
    calls: list[dict] = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "node_id": "k-1"}

    monkeypatch.setattr(design, "_memory_runner", lambda name: fake_runner)

    out = design.documenting_knowledge(_ctx(tmp_path), {"statement": "auth usa Strategy"})

    assert out.ok is True
    assert out.data["node_id"] == "k-1"
    assert calls[0]["statement"] == "auth usa Strategy"
    assert calls[0]["type"] == "design"


def test_review_code_aprueba_archivos_validos(tmp_path):
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "ok.js").write_text("const x = 1;\n", encoding="utf-8")

    out = review.review_code(_ctx(tmp_path), {"files": ["ok.py", "ok.js"]})

    assert out.ok is True
    assert out.data["passed"] is True
    assert out.data["findings"] == []


def test_review_code_detecta_error_de_sintaxis(tmp_path):
    (tmp_path / "bad.py").write_text("def x(:\n", encoding="utf-8")

    out = review.review_code(_ctx(tmp_path), {"files": ["bad.py"]})

    assert out.ok is True
    assert out.data["passed"] is False
    assert out.data["findings"][0]["file"] == "bad.py"


def test_review_code_reporta_archivo_inexistente(tmp_path):
    out = review.review_code(_ctx(tmp_path), {"files": ["nope.py"]})

    assert out.data["passed"] is False
    assert out.data["findings"][0]["error"] == "no existe"
