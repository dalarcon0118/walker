"""test_memory_health_bdd.py — salud de memoria como precondición (P1.2).

Los health checks existían (CPG/CKG) pero ninguna decisión los consumía: el
agente citaba `adr-###` de un CKG vacío como autoridad. Ahora `preflight` y
`agent_context` exponen `memory_health` con `trusted` y la cobertura de cyclo
declarada como `partial` (extractor incompleto) y no como `unavailable`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def project_dir(tmp_path: Path) -> str:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "project.json").write_text(
        json.dumps(
            {
                "project": {
                    "meta": {"project_id": "health-bdd"},
                    "location": {"repo_path": str(tmp_path)},
                }
            }
        )
    )
    (tmp_path / ".cognitive" / "projects" / "health-bdd" / "data").mkdir(parents=True)
    return str(tmp_path)


def test_cpg_health_declara_cyclo_unavailable_sin_indice(project_dir):
    from fast_mcp.features.code_analysis.application.use_cases.check_cpg_health import (
        CheckCpgHealthUseCase,
    )
    from fast_mcp.shared_kernel.project_support import ProjectService

    data_dir = str(ProjectService.resolve_paths(project_dir).data_dir)
    out = CheckCpgHealthUseCase.execute(data_dir)

    assert out["node_count"] == 0
    assert out["cyclo"]["status"] == "unavailable"
    assert out["cyclo"]["known"] == 0


def test_memory_health_vacio_no_es_confiable(project_dir):
    from fast_mcp.memories.handlers import knowledge

    knowledge._HEALTH_CACHE.clear()
    out = knowledge._memory_health(project_dir)

    assert out["ckg"]["status"] in ("empty", "error")
    assert out["cpg"]["status"] in ("empty", "error")
    assert out["trusted"] is False


def test_memory_health_reporta_partial_y_trusted(project_dir, monkeypatch):
    from returns.result import Success

    from fast_mcp.memories.handlers import knowledge

    knowledge._HEALTH_CACHE.clear()
    monkeypatch.setattr(
        "fast_mcp.features.cognitive_knowledge.application.services.ckg_read.CkgReadService.check_health",
        staticmethod(lambda data_dir: Success({"status": "12 nodes", "node_count": 12})),
    )
    monkeypatch.setattr(
        "fast_mcp.features.code_analysis.application.use_cases.check_cpg_health.CheckCpgHealthUseCase.execute",
        staticmethod(
            lambda data_dir: {
                "status": "100 nodes",
                "node_count": 100,
                "cyclo": {"known": 60, "total": 100, "status": "partial"},
            }
        ),
    )

    out = knowledge._memory_health(project_dir)

    assert out["ckg"] == {"nodes": 12, "status": "ok"}
    assert out["cpg"] == {"nodes": 100, "status": "ok"}
    assert out["cyclo"] == {"known": 60, "total": 100, "status": "partial",
                            "by_language": {}}
    assert out["trusted"] is True


def test_complexity_partial_no_nombra_un_lenguaje():
    """Walker reporta cobertura por adapter, no un lenguaje hardcodeado.

    `cyclo` es opcional en el símbolo: si el adapter del lenguaje del proyecto
    no lo produce, el estado honesto es `partial` con `languages`, sin decir
    "JS" (el mismo mensaje debe servir para cualquier proyecto).
    """
    from types import SimpleNamespace

    from fast_mcp.features.code_analysis.application.baseline_service import (
        BaselineService,
    )

    nodes = [
        SimpleNamespace(type="function", name="f", path="src/a.js", cyclo=None),
        SimpleNamespace(type="function", name="g", path="src/b.js", cyclo=None),
    ]
    out = BaselineService._complexity(nodes)

    assert out["status"] == "partial"
    assert out["known"] == 0 and out["total"] == 2
    assert out["languages"] == [".js"]
    assert "js" not in out["reason"].lower()
    assert "reindex" not in out["reason"].lower()


def test_agent_context_incluye_memory_health(project_dir, monkeypatch):
    from fast_mcp.memories.context import MemoryContext
    from fast_mcp.memories.handlers import knowledge

    knowledge._HEALTH_CACHE.clear()
    monkeypatch.setattr(
        knowledge, "_memory_health",
        lambda chain_dir: {"ckg": {"nodes": 0, "status": "empty"},
                           "cpg": {"nodes": 0, "status": "empty"},
                           "cyclo": {"known": 0, "total": 0, "status": "unavailable"},
                           "trusted": False},
    )
    ctx = MemoryContext(chain_dir=project_dir, project_root=project_dir,
                        project_id="health-bdd", data_dir=f"{project_dir}/data")

    out = knowledge.handle_agent_context(ctx, text="x")

    assert out["memory_health"]["trusted"] is False
    assert out["memory_health"]["ckg"]["status"] == "empty"


def test_preflight_incluye_memory_health(project_dir, monkeypatch):
    from fast_mcp.memories.context import MemoryContext
    from fast_mcp.memories.handlers import knowledge

    knowledge._HEALTH_CACHE.clear()
    monkeypatch.setattr(
        knowledge, "_memory_health",
        lambda chain_dir: {"ckg": {"nodes": 3, "status": "ok"},
                           "cpg": {"nodes": 9, "status": "ok"},
                           "cyclo": {"known": 9, "total": 9, "status": "full"},
                           "trusted": True},
    )

    class _Ctx:
        chain_dir = project_dir
        project_id = "health-bdd"

        def code_context_service(self):
            from types import SimpleNamespace

            return SimpleNamespace(
                execute=lambda target_file, query: {
                    "symbols": [], "relations": {"called_by": [], "calls": [],
                                                 "imports": []},
                }
            )

    out = knowledge.handle_preflight(_Ctx(), files=["a.py"])

    assert out["memory_health"]["trusted"] is True
    assert out["memory_health"]["cyclo"]["status"] == "full"
