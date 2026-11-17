"""test_spec_derive_bdd.py — criterios y precondiciones desde el pedido.

El usuario dice "sin romper los tests" o "dado que el test ya existe". El
workflow necesita eso como campos para verificarlo después. Es heurística: parte
el pedido en cláusulas y clasifica las que traen marca. Lo que no encuentra no
se inventa — un criterio falso se termina verificando contra el código y
fallando por una razón que nadie pidió.
"""

from __future__ import annotations

from fast_mcp.features.tasks.handlers.spec_derive import derive


def test_recorta_desde_la_marca_no_toda_la_frase():
    out = derive("Arreglá la cancelación de jugadas sin romper los tests de accounting")

    assert out["acceptance_criteria"] == ["sin romper los tests de accounting"]
    assert out["source"] == "heuristic"


def test_precondicion_gana_aunque_mencione_tests():
    out = derive(
        "Implementá resta(a, b) que debe devolver a - b. "
        "Dado que el test ya existe, no modifiques los tests."
    )

    assert out["preconditions"] == ["Dado que el test ya existe"]
    assert out["acceptance_criteria"] == ["debe devolver a - b", "no modifiques los tests"]


def test_pedido_sin_condiciones_deja_todo_vacio_y_marcado():
    out = derive("Agregá a b.py una función triple(a) que multiplique por tres.")

    assert out == {"acceptance_criteria": [], "preconditions": [], "source": "empty"}


def test_raices_para_conjugaciones():
    """"manteniendo"/"preservando" no contienen "mantener"/"preservar"."""
    out = derive("Refactorizá manteniendo la API pública y sin cambiar el esquema.")

    assert out["acceptance_criteria"] == ["manteniendo la API pública", "sin cambiar el esquema"]


def test_dedupe_y_tope():
    pedido = ". ".join(["sin romper los tests"] * 10)

    out = derive(pedido)

    assert out["acceptance_criteria"] == ["sin romper los tests"]


def test_vacio_no_explota():
    assert derive("")["source"] == "empty"
    assert derive(None)["source"] == "empty"


def test_implement_task_persiste_los_criterios(monkeypatch):
    """La spec tiene que nacer con criterios, no sólo con objective y files."""
    from types import SimpleNamespace

    from fast_mcp.features.tasks.handlers import implement

    class _Svc:
        def __init__(self):
            self.specs = {"SPR-1": []}

        def get_active_spring(self):
            return {"spring_id": "SPR-1"}

        def list_springs(self):
            return [{"spring_id": "SPR-1"}]

        def create_spring(self, sid, goal=""):
            return {"spring_id": sid}

        def get_specs(self, sid):
            return self.specs[sid]

        def get_spec(self, sid, spec_id):
            return next((s for s in self.specs[sid] if s["spec_id"] == spec_id), None)

        def add_spec(self, sid, objective, files=None, **kw):
            spec = {"spring_id": sid, "spec_id": "SPEC-001", "objective": objective,
                    "files": files or [], "task_context": {}}
            self.specs[sid].append(spec)
            return spec

        def update_spec(self, sid, spec_id, updates):
            spec = self.get_spec(sid, spec_id)
            spec.update(updates)
            return spec

    from fast_mcp.features.tasks.handlers import _di

    svc = _Svc()
    monkeypatch.setattr(_di, "_SPRINT_FACTORY", lambda chain: svc)

    out = implement.implement_task(
        SimpleNamespace(chain_dir="/tmp/x", task_id="", session_id="s", cpg_adapter=None),
        {"task_id": "T-1", "objective": "Arreglá suma sin romper los tests.", "files": []},
    )

    assert out.ok
    spec = svc.specs["SPR-1"][0]
    assert spec["acceptance_criteria"] == ["sin romper los tests"]
    assert spec["task_context"]["derived"] == "heuristic"


# ── criterios → predicados ejecutables ──────────────────────────────────


def test_criterio_de_tests_se_convierte_en_comando(tmp_path):
    from fast_mcp.features.tasks.handlers.spec_derive import criteria_to_predicates

    (tmp_path / "tests").mkdir()

    out = criteria_to_predicates(["sin romper los tests existentes"], str(tmp_path))

    assert out["test_command"] == "python -m pytest -q"
    assert out["predicates"][0]["type"] == "command_exit"
    assert out["predicates"][0]["command"] == "python -m pytest -q"
    assert out["unmapped"] == []


def test_no_tocar_tests_usa_git_diff_y_se_marca_fragil(tmp_path):
    from fast_mcp.features.tasks.handlers.spec_derive import criteria_to_predicates

    out = criteria_to_predicates(["no modifiques los tests"], str(tmp_path))

    pred = out["predicates"][0]
    assert pred["command"].startswith("git diff --quiet")
    assert pred["fragile"] is True, "depende de no haber commiteado: hay que decirlo"


def test_detecta_django_y_node(tmp_path):
    from fast_mcp.features.tasks.handlers.spec_derive import criteria_to_predicates

    (tmp_path / "manage.py").write_text("")
    assert criteria_to_predicates(["los tests pasan"], str(tmp_path))["test_command"].startswith(
        "python manage.py test"
    )

    (tmp_path / "manage.py").unlink()
    (tmp_path / "package.json").write_text("{}")
    assert criteria_to_predicates(["tests pass"], str(tmp_path))["test_command"].startswith("npm test")


def test_sin_comando_inferible_queda_unmapped(tmp_path):
    from fast_mcp.features.tasks.handlers.spec_derive import criteria_to_predicates

    out = criteria_to_predicates(["que los tests pasen"], str(tmp_path))

    assert out["predicates"] == []
    assert "comando de tests" in out["unmapped"][0]["reason"]


def test_criterio_no_ejecutable_se_declara(tmp_path):
    from fast_mcp.features.tasks.handlers.spec_derive import criteria_to_predicates

    out = criteria_to_predicates(["no cambies la API pública"], str(tmp_path))

    assert out["predicates"] == []
    assert out["unmapped"][0]["criterion"] == "no cambies la API pública"


def test_no_duplica_el_mismo_comando(tmp_path):
    from fast_mcp.features.tasks.handlers.spec_derive import criteria_to_predicates

    (tmp_path / "tests").mkdir()
    out = criteria_to_predicates(
        ["sin romper los tests", "los tests deben pasar"], str(tmp_path)
    )

    assert len(out["predicates"]) == 1
