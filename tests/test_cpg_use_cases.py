"""
Tests BDD (pytest-bdd) — Use cases de aplicación del CPG.

Escenarios: tests/features/cpg_use_cases.feature

Cubre: CheckCpgHealthUseCase, GatherCpgContextUseCase, DetectUnusedFunctionsUseCase,
ReviewCodeUseCase y ProjectMetricsUseCase. Contrato: nunca lanzan y devuelven
estructuras deterministas (convention-001).
"""

from __future__ import annotations

import os

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("features/cpg_use_cases.feature")


# ── Fixtures de contexto ─────────────────────────────────────────


@pytest.fixture
def walker_chain_dir() -> str:
    """Chain dir del proyecto real walker (registrado globalmente)."""
    return os.path.join(
        os.getcwd(), ".cognitive", "projects", "walker", "data", "spring"
    )


@pytest.fixture
def walker_cpg_chain_dir(tmp_path: str) -> str:
    """Construye un CPG real mínimo en tmp y devuelve su chain_dir.

    Reutiliza el patrón de TestCpgUseCases: crea un proyecto con repo real,
    lo registra, construye el CPG y devuelve el chain_dir del sprint.
    """
    from features.code_analysis import CPGBuilder
    from features.project import ProjectService

    repo_root = os.path.join(str(tmp_path), "repo")
    src = os.path.join(repo_root, "src")
    os.makedirs(src)
    with open(os.path.join(src, "mod.py"), "w") as f:
        f.write("def add(a, b):\n    return a + b\n\nclass Service:\n    pass\n")

    cog_dir = os.path.join(repo_root, ".cognitive", "projects", "test-proj")
    os.makedirs(os.path.join(cog_dir, "data"))
    import json

    with open(os.path.join(cog_dir, "data", "project.json"), "w") as f:
        json.dump(
            {
                "project": {
                    "meta": {"project_id": "test-proj", "version": "1.0"},
                    "identity": {"name": "Test Proj", "language": "python"},
                    "location": {
                        "repo_path": repo_root,
                        "code_paths": ["src"],
                    },
                }
            },
            f,
        )
    chain_dir = os.path.join(cog_dir, "data", "spring", "sprint-1")
    os.makedirs(chain_dir)

    ProjectService.create_project(
        project_id="test-proj",
        repo_path=repo_root,
        name="Test Proj",
        language="python",
    )
    try:
        builder = CPGBuilder(repo_root)
        builder.build()
        return chain_dir
    finally:
        # limpiar registro global
        from libs.path_resolver import get_walker_root

        reg_path = get_walker_root() / "data" / "projects.json"
        if reg_path.exists():
            try:
                data = json.loads(reg_path.read_text())
                data.get("projects", {}).pop("test-proj", None)
                reg_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            except Exception:
                pass


@pytest.fixture
def uc_ctx() -> dict:
    return {}


@pytest.fixture
def cpg_ctx(walker_cpg_chain_dir: str, uc_ctx: dict) -> dict:
    """Contexto compartido con chain_dir del CPG construido."""
    uc_ctx["chain_dir"] = walker_cpg_chain_dir
    return uc_ctx


# ── Antecedentes ─────────────────────────────────────────────────


@given("un proyecto walker con CPG construido")
def _bdd_cpg_construido(cpg_ctx: dict):
    assert os.path.isdir(cpg_ctx["chain_dir"])


@given("un directorio de datos sin CPG")
def _bdd_data_sin_cpg(uc_ctx: dict, tmp_path: str):
    data_dir = os.path.join(str(tmp_path), "data")
    os.makedirs(data_dir)
    uc_ctx["data_dir"] = data_dir


@given("un chain_dir del proyecto walker registrado")
def _bdd_chain_walker(uc_ctx: dict, walker_chain_dir: str):
    uc_ctx["chain_dir"] = walker_chain_dir


# ── Health ───────────────────────────────────────────────────────


@when("se consulta la salud del CPG")
def _bdd_health(uc_ctx: dict, cpg_ctx: dict):
    from features.code_analysis.application.use_cases import CheckCpgHealthUseCase

    if "data_dir" in uc_ctx:
        uc_ctx["result"] = CheckCpgHealthUseCase.execute(uc_ctx["data_dir"])
    else:
        # resolver data_dir canónico del CPG construido
        data_dir = os.path.join(uc_ctx["chain_dir"], "..", "..")
        data_dir = os.path.abspath(data_dir)  # <cog>/data
        uc_ctx["result"] = CheckCpgHealthUseCase.execute(data_dir)


@then('el status termina en "nodes"')
def _bdd_status_nodes(uc_ctx: dict):
    assert uc_ctx["result"]["status"].endswith("nodes"), uc_ctx["result"]


@then("node_count es mayor que cero")
def _bdd_node_count_mayor(uc_ctx: dict):
    assert uc_ctx["result"]["node_count"] > 0, uc_ctx["result"]


@then("el número del status coincide con node_count")
def _bdd_status_coincide(uc_ctx: dict):
    assert int(uc_ctx["result"]["status"].split()[0]) == uc_ctx["result"]["node_count"]


@then('el status es "not scanned"')
def _bdd_not_scanned(uc_ctx: dict):
    assert uc_ctx["result"]["status"] == "not scanned"


@then("node_count es cero")
def _bdd_node_count_cero(uc_ctx: dict):
    assert uc_ctx["result"]["node_count"] == 0


# ── Gather ───────────────────────────────────────────────────────


@when("se hace gather de contexto sin files")
def _bdd_gather_sin_files(uc_ctx: dict):
    from features.code_analysis.application.use_cases import GatherCpgContextUseCase

    uc_ctx["result"] = GatherCpgContextUseCase.execute(uc_ctx["chain_dir"], files=None)


@when('se hace gather de contexto con el file "src/mod.py"')
def _bdd_gather_con_file(uc_ctx: dict):
    from features.code_analysis.application.use_cases import GatherCpgContextUseCase

    uc_ctx["result"] = GatherCpgContextUseCase.execute(
        uc_ctx["chain_dir"], files=["src/mod.py"]
    )


@then("no hay error en el resultado")
def _bdd_sin_error(uc_ctx: dict):
    assert "error" not in uc_ctx["result"], uc_ctx["result"].get("error")


@then("files_analyzed es cero")
def _bdd_files_analyzed_cero(uc_ctx: dict):
    assert uc_ctx["result"]["files_analyzed"] == 0, uc_ctx["result"]


@then('la key "structure" está presente')
def _bdd_structure_presente(uc_ctx: dict):
    assert "structure" in uc_ctx["result"]


@then("files_analyzed es uno")
def _bdd_files_analyzed_uno(uc_ctx: dict):
    assert uc_ctx["result"]["files_analyzed"] == 1, uc_ctx["result"]


@then("files es un dict")
def _bdd_files_dict(uc_ctx: dict):
    assert isinstance(uc_ctx["result"]["files"], dict)


# ── DetectUnused ─────────────────────────────────────────────────


@when("se detectan funciones sin uso")
def _bdd_detect_unused(uc_ctx: dict):
    from features.code_analysis.application.use_cases import (
        DetectUnusedFunctionsUseCase,
    )

    uc_ctx["result"] = DetectUnusedFunctionsUseCase.execute(uc_ctx["chain_dir"])


@then('el resultado expone la query "bug:unused_functions"')
def _bdd_unused_query(uc_ctx: dict):
    assert uc_ctx["result"]["query"] == "bug:unused_functions", uc_ctx["result"]


@then("el total es un entero")
def _bdd_unused_total(uc_ctx: dict):
    if "total" in uc_ctx["result"]:
        assert isinstance(uc_ctx["result"]["total"], int)
    else:
        assert uc_ctx["result"].get("message") == "No unused functions found"


@then("no hay errores")
def _bdd_sin_errores(uc_ctx: dict):
    assert "error" not in uc_ctx["result"]


# ── Review ───────────────────────────────────────────────────────


@when("se ejecuta el review de código")
def _bdd_review(uc_ctx: dict):
    from features.code_analysis.application.use_cases import ReviewCodeUseCase

    uc_ctx["result"] = ReviewCodeUseCase.execute(uc_ctx["chain_dir"])


@then("el reporte incluye bug_proposals")
def _bdd_review_bp(uc_ctx: dict):
    assert "bug_proposals" in uc_ctx["result"], list(uc_ctx["result"].keys())


@then("bug_proposals expone total_findings entero")
def _bdd_bp_total(uc_ctx: dict):
    bp = uc_ctx["result"]["bug_proposals"]
    assert isinstance(bp["total_findings"], int)


@then("bug_proposals incluye by_type y findings")
def _bdd_bp_estructura(uc_ctx: dict):
    bp = uc_ctx["result"]["bug_proposals"]
    assert "by_type" in bp and isinstance(bp["by_type"], dict)
    assert "findings" in bp and isinstance(bp["findings"], list)


@then("second_report es coherente con bug_proposals")
def _bdd_bp_coherente(uc_ctx: dict):
    sr = uc_ctx["result"]["second_report"]
    assert sr["total_findings"] == uc_ctx["result"]["bug_proposals"]["total_findings"]
    assert "summary" in sr and isinstance(sr["summary"], str)


# ── Metrics ──────────────────────────────────────────────────────


@when("se computan las métricas del proyecto")
def _bdd_metrics(uc_ctx: dict):
    from features.code_analysis.application.use_cases import ProjectMetricsUseCase

    uc_ctx["result"] = ProjectMetricsUseCase.execute(uc_ctx["chain_dir"])


@then("el resultado incluye metrics y gates")
def _bdd_metrics_keys(uc_ctx: dict):
    assert "metrics" in uc_ctx["result"], list(uc_ctx["result"].keys())
    assert "gates" in uc_ctx["result"], list(uc_ctx["result"].keys())


@then("las métricas reportan archivos y LOC mayores a cero")
def _bdd_metrics_pobladas(uc_ctx: dict):
    metrics = uc_ctx["result"].get("metrics", {})
    assert metrics.get("files", 0) > 0, metrics
    assert metrics.get("loc", 0) > 0, metrics


@then("los gates incluyen cyclomatic_complexity y dead_code")
def _bdd_gates_nombres(uc_ctx: dict):
    gates = uc_ctx["result"].get("gates", [])
    assert len(gates) >= 3, gates
    names = {g.get("name") for g in gates}
    assert "cyclomatic_complexity" in names
    assert "dead_code" in names


@then("cada gate expone threshold, actual y passed")
def _bdd_gates_criterios(uc_ctx: dict):
    for g in uc_ctx["result"].get("gates", []):
        assert "threshold" in g and "actual" in g
        assert "passed" in g
