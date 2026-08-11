"""Tests BDD (pytest-bdd) — Use cases de delta tracking.

Feature value: CreateSpringUseCase, UpdateSpringUseCase, AddSpecUseCase,
UpsertSpecsUseCase, QueryDeltaUseCase, GetSprintProgressUseCase, presenters,
projections y domain values operan correctamente con un repositorio en memoria
aislando la logica de negocio.

Escenarios: bdd/features/delta_use_cases.feature
"""

from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/delta_use_cases.feature")


@pytest.fixture
def delta_ctx() -> dict:
    return {}


class InMemoryDeltaRepository:
    def __init__(self):
        from features.delta_tracking.domain.entities import Spring, Spec

        self.springs: dict[str, Spring] = {}
        self.specs: dict[str, Spec] = {}
        self.edges: list[tuple[str, str, str]] = []

    def get_spring(self, spring_id: str):
        return self.springs.get(spring_id)

    def save_spring(self, spring) -> None:
        self.springs[spring.spring_id] = spring

    def list_springs(self, status: str = ""):
        result = list(self.springs.values())
        if status:
            result = [s for s in result if s.status == status]
        return result

    def get_active_spring(self):
        for s in self.springs.values():
            if s.status == "active":
                return s
        return None

    def get_specs(self, spring_id: str, status: str = ""):
        result = [s for s in self.specs.values() if s.spring_id == spring_id]
        if status:
            result = [s for s in result if s.status == status]
        return sorted(result, key=lambda s: s.spec_index)

    def get_spec(self, spring_id: str, spec_id: str):
        return self.specs.get(f"{spring_id}:{spec_id}")

    def save_spec(self, spec) -> None:
        self.specs[spec.doc_id] = spec

    def save_specs_batch(self, specs) -> None:
        for spec in specs:
            self.save_spec(spec)

    def delete_spec(self, spring_id: str, spec_id: str) -> None:
        self.specs.pop(f"{spring_id}:{spec_id}", None)

    def add_edge(self, from_id: str, to_id: str, edge_type: str) -> None:
        self.edges.append((from_id, to_id, edge_type))


@given("que existe un repositorio en memoria vacio")
def _empty_repo(delta_ctx: dict):
    delta_ctx["repo"] = InMemoryDeltaRepository()


@given("un repositorio en memoria vacio")
def _empty_repo(delta_ctx: dict):
    delta_ctx["repo"] = InMemoryDeltaRepository()


@given("un repositorio con un spring en planning")
def _repo_with_spring_planning(delta_ctx: dict):
    from features.delta_tracking.application.use_cases.create_spring import (
        CreateSpringUseCase,
    )

    repo = InMemoryDeltaRepository()
    uc = CreateSpringUseCase(repo)
    uc.execute("sprint-a", goal="Goal demo", status="planning", project_id="p")
    delta_ctx["repo"] = repo


@given("un repositorio con un spring existente")
def _repo_with_spring(delta_ctx: dict):
    from features.delta_tracking.application.use_cases.create_spring import (
        CreateSpringUseCase,
    )

    repo = InMemoryDeltaRepository()
    uc = CreateSpringUseCase(repo)
    uc.execute("sprint-a", goal="Goal demo", status="planning", project_id="p")
    delta_ctx["repo"] = repo


@given("un repositorio vacio")
def _repo_empty(delta_ctx: dict):
    delta_ctx["repo"] = InMemoryDeltaRepository()


@given("un spring con 3 specs: 1 completed, 1 in_progress, 1 draft")
def _spring_with_three_specs(delta_ctx: dict):
    from features.delta_tracking.application.use_cases.create_spring import (
        CreateSpringUseCase,
    )
    from features.delta_tracking.application.use_cases.manage_specs import (
        AddSpecUseCase,
    )

    repo = InMemoryDeltaRepository()
    uc = CreateSpringUseCase(repo)
    uc.execute("sprint-a", goal="Goal demo", status="active", project_id="p")
    AddSpecUseCase(repo).execute("sprint-a", "T1", spec_index=0, status="completed")
    AddSpecUseCase(repo).execute("sprint-a", "T2", spec_index=1, status="in_progress")
    AddSpecUseCase(repo).execute("sprint-a", "T3", spec_index=2, status="draft")
    delta_ctx["repo"] = repo


@given("un servicio que lanza RuntimeError")
def _boom_service(delta_ctx: dict):
    class BoomSvc:
        def get_spring_id(self, chain_dir: str) -> str:
            raise RuntimeError("delta corrupto")

        def ensure_spring_imported(self, chain_dir: str, spring_id: str):
            raise RuntimeError("delta corrupto")

    delta_ctx["boom_svc"] = BoomSvc()


@when("ejecuto CreateSpringUseCase con status active sobre sprint-a")
def _create_active(delta_ctx: dict):
    from features.delta_tracking.application.use_cases.create_spring import (
        CreateSpringUseCase,
    )

    uc = CreateSpringUseCase(delta_ctx["repo"])
    delta_ctx["spring"] = uc.execute(
        "sprint-a", goal="Goal", status="active", project_id="p"
    )


@then("el spring tiene active True")
def _assert_spring_active(delta_ctx: dict):
    assert delta_ctx["spring"].active is True


@then("el repositorio persiste active True")
def _assert_repo_active(delta_ctx: dict):
    assert delta_ctx["repo"].springs["sprint-a"].active is True


@when("ejecuto CreateSpringUseCase con status planning sobre sprint-b")
def _create_planning(delta_ctx: dict):
    from features.delta_tracking.application.use_cases.create_spring import (
        CreateSpringUseCase,
    )

    uc = CreateSpringUseCase(delta_ctx["repo"])
    delta_ctx["spring2"] = uc.execute(
        "sprint-b", goal="Goal", status="planning", project_id="p"
    )


@then("el spring-b tiene active False")
def _assert_spring_inactive(delta_ctx: dict):
    assert delta_ctx["spring2"].active is False


@when("ejecuto set_status a active")
def _set_status_active(delta_ctx: dict):
    from features.delta_tracking.application.use_cases.update_spring import (
        UpdateSpringUseCase,
    )

    uc = UpdateSpringUseCase(delta_ctx["repo"])
    uc.set_status("sprint-a", "active")


@then("el spring tiene status active y active True")
def _assert_after_active(delta_ctx: dict):
    spring = delta_ctx["repo"].springs["sprint-a"]
    assert spring.status == "active"
    assert spring.active is True


@when("ejecuto set_status a closed")
def _set_status_closed(delta_ctx: dict):
    from features.delta_tracking.application.use_cases.update_spring import (
        UpdateSpringUseCase,
    )

    uc = UpdateSpringUseCase(delta_ctx["repo"])
    uc.set_status("sprint-a", "closed")


@then("el spring tiene active False")
def _assert_after_closed(delta_ctx: dict):
    spring = delta_ctx["repo"].springs["sprint-a"]
    assert spring.active is False


@when("ejecuto set_context con dict posicional")
def _set_context_dict(delta_ctx: dict):
    from features.delta_tracking.application.use_cases.update_spring import (
        UpdateSpringUseCase,
    )

    uc = UpdateSpringUseCase(delta_ctx["repo"])
    uc.set_context("sprint-a", {"current_spec_index": 0})


@then("el context se actualiza")
def _assert_context_updated(delta_ctx: dict):
    spring = delta_ctx["repo"].springs["sprint-a"]
    assert spring.context.get("current_spec_index") == 0


@when("ejecuto set_context con kwargs sueltos")
def _set_context_kwargs(delta_ctx: dict):
    from features.delta_tracking.application.use_cases.update_spring import (
        UpdateSpringUseCase,
    )

    uc = UpdateSpringUseCase(delta_ctx["repo"])
    uc.set_context("sprint-a", current_spec_index=1, current_task_index=1)


@then("el context se actualiza con los kwargs")
def _assert_context_kwargs(delta_ctx: dict):
    spring = delta_ctx["repo"].springs["sprint-a"]
    assert spring.context == {"current_spec_index": 1, "current_task_index": 1}


@when("ejecuto execute con goal, spring_id inmutable y display_name None")
def _execute_ignore_immutables(delta_ctx: dict):
    from features.delta_tracking.application.use_cases.update_spring import (
        UpdateSpringUseCase,
    )

    uc = UpdateSpringUseCase(delta_ctx["repo"])
    uc.execute(
        "sprint-a",
        {
            "goal": "Nuevo goal",
            "spring_id": "HACK",
            "display_name": None,
        },
    )


@then("el goal se actualiza")
def _assert_goal_updated(delta_ctx: dict):
    spring = delta_ctx["repo"].springs["sprint-a"]
    assert spring.goal == "Nuevo goal"


@then("el spring_id no cambia")
def _assert_id_unchanged(delta_ctx: dict):
    spring = delta_ctx["repo"].springs["sprint-a"]
    assert spring.spring_id == "sprint-a", "spring_id no debe mutarse"


@then("display_name queda vacio")
def _assert_display_name_empty(delta_ctx: dict):
    spring = delta_ctx["repo"].springs["sprint-a"]
    assert spring.display_name == ""


@when("ejecuto execute sobre un id inexistente")
def _execute_nonexistent(delta_ctx: dict):
    from features.delta_tracking.application.use_cases.update_spring import (
        UpdateSpringUseCase,
    )

    uc = UpdateSpringUseCase(delta_ctx["repo"])
    delta_ctx["result"] = uc.execute("no-existe", {"goal": "x"})


@then("retorna None")
def _assert_none(delta_ctx: dict):
    assert delta_ctx["result"] is None


@when("ejecuto set_status sobre un id inexistente")
def _set_status_nonexistent(delta_ctx: dict):
    from features.delta_tracking.application.use_cases.update_spring import (
        UpdateSpringUseCase,
    )

    uc = UpdateSpringUseCase(delta_ctx["repo"])
    delta_ctx["status_result"] = uc.set_status("no-existe", "active")


@then("set_status retorna None")
def _assert_set_status_none(delta_ctx: dict):
    assert delta_ctx["status_result"] is None


@when("ejecuto AddSpecUseCase con objetivo y dominio")
def _add_spec(delta_ctx: dict):
    from features.delta_tracking.application.use_cases.manage_specs import (
        AddSpecUseCase,
    )

    uc = AddSpecUseCase(delta_ctx["repo"])
    delta_ctx["spec"] = uc.execute(
        "sprint-a", "Implementar auth", domain="security", spec_index=0
    )


@then("la spec tiene spec_id SPEC-001")
def _assert_spec_id(delta_ctx: dict):
    assert delta_ctx["spec"].spec_id == "SPEC-001"


@then("el repositorio persiste el objetivo")
def _assert_persists_objective(delta_ctx: dict):
    assert delta_ctx["repo"].specs["sprint-a:SPEC-001"].objective == "Implementar auth"


@then("se crea un edge contains")
def _assert_edge_contains(delta_ctx: dict):
    assert ("sprint-a", "sprint-a:SPEC-001", "contains") in delta_ctx["repo"].edges


@when("ejecuto UpsertSpecsUseCase con dos objetivos nuevos")
def _upsert_two_new(delta_ctx: dict):
    from features.delta_tracking.application.use_cases.manage_specs import (
        UpsertSpecsUseCase,
    )

    uc = UpsertSpecsUseCase(delta_ctx["repo"])
    delta_ctx["r1"] = uc.execute(
        "sprint-a",
        [
            {"objective": "Fix auth bug", "domain": "security", "type": "feature"},
            {"objective": "Add logout", "domain": "security", "type": "feature"},
        ],
    )


@then("se crean 2 specs y 0 se refinan")
def _assert_created_2(delta_ctx: dict):
    assert delta_ctx["r1"]["created"] == 2
    assert delta_ctx["r1"]["refined"] == 0


@when("ejecuto UpsertSpecsUseCase con un objetivo existente para refinar")
def _upsert_refine(delta_ctx: dict):
    from features.delta_tracking.application.use_cases.manage_specs import (
        UpsertSpecsUseCase,
    )

    uc = UpsertSpecsUseCase(delta_ctx["repo"])
    delta_ctx["r2"] = uc.execute(
        "sprint-a",
        [{"objective": "Fix auth bug", "status": "specified", "result_state": "ok"}],
    )


@then("se crean 0 specs y 1 se refina")
def _assert_refined_1(delta_ctx: dict):
    assert delta_ctx["r2"]["created"] == 0
    assert delta_ctx["r2"]["refined"] == 1


@then("no hay specs duplicadas")
def _assert_no_duplicates(delta_ctx: dict):
    assert len(delta_ctx["repo"].specs) == 2, "no debe duplicar specs"


@then("la spec refinada tiene status specified y result_state ok")
def _assert_refined_fields(delta_ctx: dict):
    refined = delta_ctx["repo"].specs["sprint-a:SPEC-001"]
    assert refined.status == "specified"
    assert refined.result_state == "ok"


@when("ejecuto get_current_spec")
def _get_current_spec(delta_ctx: dict):
    from features.delta_tracking.application.use_cases.query_delta import (
        QueryDeltaUseCase,
    )

    uc = QueryDeltaUseCase(delta_ctx["repo"])
    delta_ctx["current"] = uc.get_current_spec("sprint-a")


@then("devuelve la spec in_progress")
def _assert_current_in_progress(delta_ctx: dict):
    assert delta_ctx["current"].spec_id == "SPEC-002", delta_ctx["current"].spec_id


@given("un spring con una spec Fix auth")
def _spring_with_fix_auth(delta_ctx: dict):
    from features.delta_tracking.application.use_cases.create_spring import (
        CreateSpringUseCase,
    )
    from features.delta_tracking.application.use_cases.manage_specs import (
        AddSpecUseCase,
    )

    repo = InMemoryDeltaRepository()
    uc = CreateSpringUseCase(repo)
    uc.execute("sprint-a", goal="Goal demo", status="active", project_id="p")
    AddSpecUseCase(repo).execute("sprint-a", "  Fix auth  ", spec_index=0)
    delta_ctx["repo"] = repo


@when("busco por objetivo exacto")
def _find_exact(delta_ctx: dict):
    from features.delta_tracking.application.use_cases.query_delta import (
        QueryDeltaUseCase,
    )

    uc = QueryDeltaUseCase(delta_ctx["repo"])
    delta_ctx["found"] = uc.find_spec_by_objective("sprint-a", "Fix auth")


@then("encuentra la spec")
def _assert_found(delta_ctx: dict):
    assert delta_ctx["found"] is not None


@when("busco en minusculas")
def _find_lower(delta_ctx: dict):
    from features.delta_tracking.application.use_cases.query_delta import (
        QueryDeltaUseCase,
    )

    uc = QueryDeltaUseCase(delta_ctx["repo"])
    delta_ctx["not_found"] = uc.find_spec_by_objective("sprint-a", "fix auth")


@then("no encuentra la spec")
def _assert_not_found(delta_ctx: dict):
    assert delta_ctx["not_found"] is None


@when("busco por cadena vacia")
def _find_empty(delta_ctx: dict):
    from features.delta_tracking.application.use_cases.query_delta import (
        QueryDeltaUseCase,
    )

    uc = QueryDeltaUseCase(delta_ctx["repo"])
    delta_ctx["empty_result"] = uc.find_spec_by_objective("sprint-a", "")


@then("no encuentra la spec por vacio")
def _assert_empty_not_found(delta_ctx: dict):
    assert delta_ctx["empty_result"] is None


@when("ejecuto get_sprint_progress")
def _get_progress(delta_ctx: dict):
    from features.delta_tracking.application.use_cases.progress import (
        GetSprintProgressUseCase,
    )

    uc = GetSprintProgressUseCase(delta_ctx["repo"])
    delta_ctx["progress"] = uc.execute("sprint-a")


@then("total_specs es 3")
def _assert_total_3(delta_ctx: dict):
    assert delta_ctx["progress"]["total_specs"] == 3


@then("completed es 1")
def _assert_completed_1(delta_ctx: dict):
    assert delta_ctx["progress"]["completed"] == 1


@then("in_progress es 1")
def _assert_in_progress_1(delta_ctx: dict):
    assert delta_ctx["progress"]["in_progress"] == 1


@then("remaining es 2")
def _assert_remaining_2(delta_ctx: dict):
    assert delta_ctx["progress"]["remaining"] == 2


@then("progress_pct es 33.3")
def _assert_pct_33(delta_ctx: dict):
    assert delta_ctx["progress"]["progress_pct"] == 33.3


@when("derivo display_name de un goal con dos frases")
def _derive_two_phrases(delta_ctx: dict):
    from features.delta_tracking.application.presenters import derive_display_name

    delta_ctx["name"] = derive_display_name(
        "Rediseño del contrato: resolver gaps", "s-x"
    )


@then("devuelve la primera frase")
def _assert_first_phrase(delta_ctx: dict):
    assert delta_ctx["name"] == "Rediseño del contrato"


@when("derivo display_name de un goal vacio")
def _derive_empty(delta_ctx: dict):
    from features.delta_tracking.application.presenters import derive_display_name

    delta_ctx["empty_name"] = derive_display_name("", "spring-1")


@then("devuelve el spring_id")
def _assert_fallback_id(delta_ctx: dict):
    assert delta_ctx["empty_name"] == "spring-1"


@when("derivo display_name de un goal muy largo")
def _derive_long(delta_ctx: dict):
    from features.delta_tracking.application.presenters import derive_display_name

    long_goal = "a" * 80
    delta_ctx["long_name"] = derive_display_name(long_goal, "s")


@then("el resultado tiene longitud <= 60")
def _assert_max_60(delta_ctx: dict):
    assert len(delta_ctx["long_name"]) <= 60


@given("un doc spring con goal pero sin display_name")
def _doc_with_goal(delta_ctx: dict):
    delta_ctx["doc"] = {
        "_id": "spring-1",
        "goal": "Refactor Fase 3: estructural",
        "status": "active",
    }


@when("aplico with_display_name")
def _apply_display_name(delta_ctx: dict):
    from features.delta_tracking.application.presenters import with_display_name

    delta_ctx["out"] = with_display_name(delta_ctx["doc"])


@then("el doc tiene display_name con la primera frase del goal")
def _assert_display_name_first_phrase(delta_ctx: dict):
    assert delta_ctx["out"]["display_name"] == "Refactor Fase 3"


@when("aplico with_display_name a None")
def _apply_none(delta_ctx: dict):
    from features.delta_tracking.application.presenters import with_display_name

    delta_ctx["none_out"] = with_display_name(None)


@then("devuelve None")
def _assert_none_out(delta_ctx: dict):
    assert delta_ctx["none_out"] is None


@given("un doc spring con display_name ya presente")
def _doc_with_existing_display_name(delta_ctx: dict):
    delta_ctx["doc2"] = {"display_name": "Ya tiene", "goal": "Otro"}


@when("aplico with_display_name al doc con display_name existente")
def _apply_existing(delta_ctx: dict):
    from features.delta_tracking.application.presenters import with_display_name

    delta_ctx["existing_out"] = with_display_name(delta_ctx["doc2"])


@then("no pisa el display_name existente")
def _assert_keep_existing(delta_ctx: dict):
    assert delta_ctx["existing_out"]["display_name"] == "Ya tiene"


@given("una spec con archivos impactados")
def _spec_with_files(delta_ctx: dict):
    from features.delta_tracking.domain.entities import Spec

    delta_ctx["spec"] = Spec(
        spring_id="sprint-a",
        objective="Fix auth",
        domain="security",
        spec_type="feature",
        spec_index=0,
        result_state="ok",
        files=["src/auth.py"],
        status="specified",
    )


@when("proyecto a task dict")
def _project_to_dict(delta_ctx: dict):
    from features.delta_tracking.application.projections import spec_to_task_dict

    delta_ctx["d"] = spec_to_task_dict(delta_ctx["spec"])


@then("task_id es SPEC-001")
def _assert_task_id(delta_ctx: dict):
    assert delta_ctx["d"]["task_id"] == "SPEC-001"


@then("spec_id es SPEC-001")
def _assert_spec_id_proj(delta_ctx: dict):
    assert delta_ctx["d"]["spec_id"] == "SPEC-001"


@then("spec_index es 0")
def _assert_spec_index(delta_ctx: dict):
    assert delta_ctx["d"]["spec_index"] == 0


@then("impacted_artifacts tiene files")
def _assert_impacted_files(delta_ctx: dict):
    assert delta_ctx["d"]["impacted_artifacts"] == {"files": ["src/auth.py"]}


@then("type es feature")
def _assert_type_feature(delta_ctx: dict):
    assert delta_ctx["d"]["type"] == "feature"


@when("ejecuto load_spring_tasks")
def _load_spring_tasks(delta_ctx: dict):
    from features.delta_tracking.application.projections import load_spring_tasks

    delta_ctx["tasks"] = load_spring_tasks(delta_ctx["boom_svc"], "/tmp/x")


@then("devuelve [] sin lanzar excepcion")
def _assert_empty_list(delta_ctx: dict):
    assert delta_ctx["tasks"] == []


@when("extraigo de /a/b/sprint-demo")
def _extract_full(delta_ctx: dict):
    from features.delta_tracking.domain.values import spring_id_from_chain_dir

    delta_ctx["sid"] = spring_id_from_chain_dir("/a/b/sprint-demo")


@then("devuelve sprint-demo")
def _assert_sprint_demo(delta_ctx: dict):
    assert delta_ctx["sid"] == "sprint-demo"


@when("extraigo de /a/b/sprint-demo/")
def _extract_trailing(delta_ctx: dict):
    from features.delta_tracking.domain.values import spring_id_from_chain_dir

    delta_ctx["sid2"] = spring_id_from_chain_dir("/a/b/sprint-demo/")


@then("devuelve sprint-demo con trailing slash")
def _assert_sprint_demo_trailing(delta_ctx: dict):
    assert delta_ctx["sid2"] == "sprint-demo"


@when("extraigo de sprint-demo")
def _extract_bare(delta_ctx: dict):
    from features.delta_tracking.domain.values import spring_id_from_chain_dir

    delta_ctx["sid3"] = spring_id_from_chain_dir("sprint-demo")


@then("devuelve sprint-demo bare")
def _assert_sprint_demo_bare(delta_ctx: dict):
    assert delta_ctx["sid3"] == "sprint-demo"
