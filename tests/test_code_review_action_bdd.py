"""BDD: CODE_REVIEW_SCAN revisa con el CPG, no solo la sintaxis.

Medido 2026-11-18: la acción `review_code` del workflow hacía ``py_compile`` /
``node --check`` y devolvía "1 archivos revisados, 0 hallazgos". El prompt, en
cambio, prometía métricas y quality gates del CPG. El resultado fue una
revisión de código que nunca miró el estado del código.

La acción ahora tiene DOS capas: sintaxis (barata, precondición) y el puerto
`ctx.cpg_adapter` (métricas, gates, dead code y bugs del CPG). Se usa el puerto
inyectado, no un import cruzado entre features (R1).

Decisión de ``passed``: mandan los hallazgos de los archivos de la TAREA. Los
gates del proyecto se reportan como ``advisory`` porque ``dead_code`` se
calcula sobre todo el repo y hoy arrastra falsos positivos (métodos contados
como funciones sin callers).
"""
from __future__ import annotations

from types import SimpleNamespace

from fast_mcp.features.tasks.handlers.review import review_code


class _FakeCpg:
    def __init__(self, report=None, error=None):
        self.report = report
        self.error = error
        self.calls: list[list[str]] = []

    def review_code(self, files=None, thresholds=None):
        self.calls.append(list(files or []))
        if self.error:
            raise self.error
        return self.report


def _ctx(chain_dir, task_id="T-1", cpg=None):
    return SimpleNamespace(chain_dir=str(chain_dir), task_id=task_id, cpg_adapter=cpg)


def _report(findings=None, gates=None, issues=None, second=None):
    return {
        "summary": "informe del CPG",
        "files_scanned": ["app.py"],
        "metrics": {"files": 10, "loc": 100},
        "gates": gates if gates is not None else [],
        "findings": findings or {},
        "issues": issues or [],
        "second_report": second or {"total_findings": 0, "findings": []},
    }


def test_sin_hallazgos_pasa_y_reporta_el_cpg(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    cpg = _FakeCpg(_report())

    result = review_code(_ctx(tmp_path, cpg=cpg), {"files": ["app.py"]})

    assert result.data["passed"] is True
    assert result.data["cpg"]["available"] is True
    assert result.data["cpg"]["files_scanned"] == ["app.py"]
    assert "CPG" in result.data["summary"]
    assert cpg.calls and cpg.calls[0][0].endswith("app.py")


def test_un_hallazgo_en_el_archivo_de_la_tarea_falla(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    cpg = _FakeCpg(
        _report(
            findings={
                "dead_code": [
                    {"file": str(tmp_path / "app.py"), "function": "f"},
                ]
            },
        )
    )

    result = review_code(_ctx(tmp_path, cpg=cpg), {"files": ["app.py"]})

    assert result.data["passed"] is False
    assert result.data["findings"][0]["source"] == "cpg"
    assert result.data["findings"][0]["function"] == "f"


def test_un_hallazgo_fuera_de_la_tarea_no_falla_pero_se_cuenta(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    cpg = _FakeCpg(
        _report(
            findings={
                "dead_code": [
                    {"file": str(tmp_path / "otro.py"), "function": "ajeno"},
                ]
            },
        )
    )

    result = review_code(_ctx(tmp_path, cpg=cpg), {"files": ["app.py"]})

    assert result.data["passed"] is True
    assert result.data["cpg"]["project_findings"] == 1
    assert result.data["cpg"]["findings"] == {}


def test_los_gates_del_proyecto_son_advisory(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    cpg = _FakeCpg(
        _report(
            gates=[
                {"name": "dead_code", "actual": 900, "threshold": 50, "passed": False},
            ],
        )
    )

    result = review_code(_ctx(tmp_path, cpg=cpg), {"files": ["app.py"]})

    # El gate del proyecto se ve, pero no bloquea una tarea que no lo causó.
    assert result.data["passed"] is True
    gate = result.data["cpg"]["gates"][0]
    assert gate["advisory"] is True and gate["scope"] == "project"
    assert "dead_code=900 (advisory)" in result.data["summary"]


def test_si_el_cpg_no_esta_la_revision_no_bloquea(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    cpg = _FakeCpg(error=RuntimeError("cpg caído"))

    result = review_code(_ctx(tmp_path, cpg=cpg), {"files": ["app.py"]})

    assert result.data["passed"] is True
    assert result.data["cpg"]["available"] is False
    assert "CPG no disponible" in result.data["summary"]


def test_sin_adapter_tampoco_bloquea(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")

    result = review_code(_ctx(tmp_path, cpg=None), {"files": ["app.py"]})

    assert result.data["passed"] is True
    assert result.data["cpg"]["available"] is False
    assert "cpg_adapter no inyectado" in result.data["cpg"]["error"]


def test_un_error_de_sintaxis_falla_y_no_depende_del_cpg(tmp_path):
    (tmp_path / "roto.py").write_text("def f(:\n")
    cpg = _FakeCpg(_report())

    result = review_code(_ctx(tmp_path, cpg=cpg), {"files": ["roto.py"]})

    assert result.data["passed"] is False
    assert result.data["findings"][0]["file"] == "roto.py"
    assert "error" in result.data["findings"][0]


def test_archivo_inexistente_falla(tmp_path):
    cpg = _FakeCpg(_report())

    result = review_code(_ctx(tmp_path, cpg=cpg), {"files": ["no-existe.py"]})

    assert result.data["passed"] is False
    assert result.data["findings"][0]["error"] == "no existe"


def test_sin_archivos_no_certifica_la_revision(tmp_path):
    """0 archivos revisados no es un verde (medido 2026-11-26).

    `passed = not findings` daba True por vacuidad cuando la spec no se podía
    resolver (id de spec ambiguo entre dos sprints) o la tarea no declaraba
    archivos. El resumen decía "CPG no disponible" pero `passed: true` se leía
    como revisión aprobada.
    """
    cpg = _FakeCpg(_report())

    result = review_code(_ctx(tmp_path, task_id="", cpg=cpg), {})

    assert result.data["passed"] is False
    assert "no certifica" in result.data["summary"]
    assert result.data["blocked_reason"]
    assert cpg.calls == []
