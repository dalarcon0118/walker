"""test_implement_persists_spec_bdd.py — la tarea tiene que existir en el delta.

El contexto del agente se ancla en la TAREA: de la spec salen los archivos, de
los archivos el grafo, del grafo el CKG. Pero `implement_task` sólo devolvía un
dict: el delta quedaba vacío y el ancla siempre caía al archivo.

Ahora `implement_task` persiste la spec (o la actualiza) y guarda el id real en
`task_context.task_id`, porque el `spec_id` del delta es autogenerado (SPEC-00N).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fast_mcp.features.tasks.handlers import implement


class _SprintService:
    """SprintService en memoria, suficiente para el contrato que usa el handler."""

    def __init__(self):
        self.springs = [{"spring_id": "SPR-1", "status": "planning"}]
        self.specs: dict[str, list[dict]] = {"SPR-1": []}

    def get_active_spring(self):
        return None  # como en la realidad: planning no es "active"

    def list_springs(self):
        return self.springs

    def create_spring(self, spring_id, goal=""):
        spring = {"spring_id": spring_id, "goal": goal, "status": "planning"}
        self.springs.append(spring)
        self.specs.setdefault(spring_id, [])
        return spring

    def get_specs(self, spring_id):
        return self.specs.get(spring_id, [])

    def get_spec(self, spring_id, spec_id):
        for s in self.specs.get(spring_id, []):
            if s.get("spec_id") == spec_id:
                return s
        return None

    def add_spec(self, spring_id, objective, files=None, spec_type="feature",
                 status="draft", description=""):
        n = len(self.specs.setdefault(spring_id, [])) + 1
        spec = {
            "spring_id": spring_id, "spec_id": f"SPEC-{n:03d}",
            "objective": objective, "files": files or [], "spec_type": spec_type,
            "status": status, "description": description, "task_context": {},
            "acceptance_criteria": [], "preconditions": [], "predicates": {},
        }
        self.specs[spring_id].append(spec)
        return spec

    def update_spec(self, spring_id, spec_id, updates):
        spec = self.get_spec(spring_id, spec_id)
        if not spec:
            return None
        spec.update(updates)
        return spec


@pytest.fixture()
def servicio(monkeypatch):
    svc = _SprintService()
    from fast_mcp.features.tasks.handlers import _di

    monkeypatch.setattr(_di, "_SPRINT_FACTORY", lambda chain_dir: svc)
    return svc


def _ctx():
    return SimpleNamespace(
        chain_dir="/tmp/proyecto", task_id="", session_id="ses-1", cpg_adapter=None
    )


def test_implement_task_persiste_la_spec(servicio):
    out = implement.implement_task(
        _ctx(), {"task_id": "T-42", "objective": "arreglar resta", "files": ["b.py"]}
    )

    assert out.ok
    assert out.data["spec"]["spec_id"] == "SPEC-001"
    spec = servicio.specs["SPR-1"][0]
    assert spec["status"] == "in_progress"
    assert spec["files"] == ["b.py"]


def test_guarda_el_task_id_real_en_task_context(servicio):
    implement.implement_task(_ctx(), {"task_id": "T-42", "objective": "x", "files": []})

    assert servicio.specs["SPR-1"][0]["task_context"]["task_id"] == "T-42"


def test_no_duplica_la_spec_si_se_declara_dos_veces(servicio):
    implement.implement_task(_ctx(), {"task_id": "T-42", "objective": "uno", "files": ["a.py"]})
    implement.implement_task(_ctx(), {"task_id": "T-42", "objective": "dos", "files": ["a.py", "b.py"]})

    assert len(servicio.specs["SPR-1"]) == 1
    assert servicio.specs["SPR-1"][0]["files"] == ["a.py", "b.py"]


def test_sin_factory_no_rompe_la_tool(monkeypatch):
    from fast_mcp.features.tasks.handlers import _di

    monkeypatch.setattr(_di, "_SPRINT_FACTORY", None)

    out = implement.implement_task(_ctx(), {"task_id": "T-1", "objective": "x", "files": []})

    assert out.ok, "persistir la spec no puede bloquear la implementación"
    assert "spec" not in out.data


def test_el_ancla_de_tarea_encuentra_la_spec_por_task_context(monkeypatch, servicio):
    from fast_mcp.memories.handlers import knowledge
    import fast_mcp.composition as composition

    implement.implement_task(_ctx(), {"task_id": "T-42", "objective": "x", "files": ["b.py"]})
    monkeypatch.setattr(composition, "build_sprint_service", lambda chain: servicio)

    found = knowledge._find_task_spec("/tmp/proyecto", "T-42")

    assert found and found["files"] == ["b.py"]
