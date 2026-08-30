"""Tests BDD (pytest-bdd) — Safety-net de módulos migrados.

Feature value: tras la migración de ``test_migrated_gaps.py`` (1615 líneas
imperativas) al feature declarativo ``bdd/features/migrated_gaps.feature``,
este archivo reactiva los steps que faltaron en el commit de migración
(de0ee49): el pipeline queda cubierto sin depender del legacy.

Escenarios: bdd/features/migrated_gaps.feature

Módulos bajo prueba (SSOT actual):
  - task_synthesis y ceremonies → features/delta_tracking/application/
  - verify_engine                → features/task_execution/infrastructure/
  - side_effect_contract         → features/engine/domain/
  - monads (TaskResult)          → shared/common/shared_domain/models.py
  - VOs tipados                  → shared/common/shared_domain/validated_vo.py + tasks/*.py
  - checkpoints / MCP config     → features/engine/domain/, features/cli_interface/
"""

from __future__ import annotations

import json

import pytest
from pytest_bdd import given, scenarios, then, when
from app.memory_schemas import register_default_memory_indexes

scenarios("bdd/features/migrated_gaps.feature")


@pytest.fixture
def mg_ctx() -> dict:
    return {}


def _make_proj(tmp) -> None:
    """Crea un mini proyecto con src/foo.py con símbolos reales."""
    (tmp / "src").mkdir(exist_ok=True)
    (tmp / "src" / "foo.py").write_text(
        "def hello_world():\n    return 42\n\n"
        "class Greeter:\n    def greet(self):\n        return 'hi'\n"
    )


# ── 1. task_synthesis (predicates → tareas) ───────────────────────


@given("los predicates de entrada con must_exist, must_not_exist e invariants")
def _predicates_input(mg_ctx: dict):
    mg_ctx["predicates"] = {
        "must_exist": ["module auth"],
        "must_not_exist": ["legacy config (deprecated)"],
        "invariants": ["sempre true"],
    }


@given("los archivos conocidos del proyecto")
def _known_files(mg_ctx: dict):
    mg_ctx["known_files"] = ["src/auth.py", "src/config.py"]


@given("archivos conocidos")
def _known_files_short(mg_ctx: dict):
    mg_ctx["known_files"] = ["src/auth.py"]


@when("genero tareas desde los predicates")
def _gen_tasks(mg_ctx: dict):
    from features.delta_tracking.application.task_synthesis import (
        generate_tasks_from_predicates,
    )

    mg_ctx["tasks"] = generate_tasks_from_predicates(
        mg_ctx["predicates"], "proj", known_files=mg_ctx.get("known_files", [])
    )


@then("obtengo 3 tipos: feature, bugfix y test")
def _three_types(mg_ctx: dict):
    types = {t["type"] for t in mg_ctx["tasks"]}
    assert types == {"feature", "bugfix", "test"}


@then('la feature tiene "auth" en el objetivo')
def _feature_objective(mg_ctx: dict):
    feature = next(t for t in mg_ctx["tasks"] if t["type"] == "feature")
    assert "auth" in feature["objective"]


@then("la feature tiene archivos impactados")
def _feature_files(mg_ctx: dict):
    feature = next(t for t in mg_ctx["tasks"] if t["type"] == "feature")
    assert feature["impacted_artifacts"]["files"]


@then('el bugfix tiene validación que empieza por "NO "')
def _bugfix_validation(mg_ctx: dict):
    bugfix = next(t for t in mg_ctx["tasks"] if t["type"] == "bugfix")
    assert bugfix["validation"]["predicates"][0].startswith("NO ")


@then('el test tiene validación que empieza por "invariant:"')
def _test_validation(mg_ctx: dict):
    test = next(t for t in mg_ctx["tasks"] if t["type"] == "test")
    assert test["validation"]["predicates"][0].startswith("invariant:")


@given("predicates vacíos")
def _predicates_empty(mg_ctx: dict):
    mg_ctx["predicates"] = {
        "must_exist": [],
        "must_not_exist": [],
        "invariants": [],
    }


@when("genero tareas desde predicates vacíos")
def _gen_tasks_empty(mg_ctx: dict):
    from features.delta_tracking.application.task_synthesis import (
        generate_tasks_from_predicates,
    )

    mg_ctx["tasks"] = generate_tasks_from_predicates(mg_ctx["predicates"], "proj")


@then("obtengo exactamente 1 tarea")
def _one_task(mg_ctx: dict):
    assert len(mg_ctx["tasks"]) == 1


@then('la tarea tiene task_id "TASK-001"')
def _task_id(mg_ctx: dict):
    assert mg_ctx["tasks"][0]["task_id"] == "TASK-001"


@then("la tarea es de tipo feature")
def _task_type_feature(mg_ctx: dict):
    assert mg_ctx["tasks"][0]["type"] == "feature"


@given("tareas generadas desde predicates")
def _tasks_generated(mg_ctx: dict):
    from features.delta_tracking.application.task_synthesis import (
        generate_tasks_from_predicates,
    )

    mg_ctx["tasks"] = generate_tasks_from_predicates(
        {"must_exist": ["module auth"], "must_not_exist": [], "invariants": []},
        "proj",
    )


@when("completo los contextos de las tareas")
def _fill_contexts(mg_ctx: dict):
    from features.delta_tracking.application.task_synthesis import (
        fill_task_contexts,
    )

    fill_task_contexts(mg_ctx["tasks"], "mejorar auth", "proj")


@then("cada tarea tiene observation, task_description y acceptance_criteria")
def _contexts_complete(mg_ctx: dict):
    for t in mg_ctx["tasks"]:
        assert t["task_context"]["observation"]
        assert t["task_description"]
        assert t["acceptance_criteria"]


@given("una tarea genérica desde el backlog")
def _generic_task(mg_ctx: dict):
    mg_ctx["tasks"] = [
        {
            "task_id": "TASK-001",
            "type": "feature",
            "objective": "Implementar module auth",
            "result_state": "done",
            "domain": "core",
            "validation": {"predicates": []},
            "impacted_artifacts": {"files": []},
            "risks": {"curvature": 0.20},
        }
    ]


@given("predicates con must_exist")
def _predicates_must_exist(mg_ctx: dict):
    mg_ctx["predicates"] = {
        "must_exist": ["module auth"],
        "must_not_exist": [],
        "invariants": [],
    }


@when("enriquezco la tarea con predicates")
def _enrich_tasks(mg_ctx: dict):
    from features.delta_tracking.application.task_synthesis import (
        enrich_tasks_with_predicates,
    )

    mg_ctx["tasks"] = enrich_tasks_with_predicates(
        mg_ctx["tasks"], mg_ctx["predicates"], known_files=["src/auth.py"]
    )


@then("la tarea tiene predicates de validación")
def _task_has_predicates(mg_ctx: dict):
    assert mg_ctx["tasks"][0]["validation"]["predicates"]


@then("los archivos impactados son los conocidos")
def _task_files_known(mg_ctx: dict):
    assert mg_ctx["tasks"][0]["impacted_artifacts"]["files"] == ["src/auth.py"]


@given("pocos predicates")
def _few_predicates(mg_ctx: dict):
    mg_ctx["low_preds"] = {
        "must_exist": ["a"],
        "must_not_exist": [],
        "invariants": [],
    }
    mg_ctx["high_preds"] = {
        "must_exist": ["a", "b", "c", "d", "e", "f", "g", "h", "i"],
        "must_not_exist": ["x", "y"],
        "invariants": [],
    }


@when("calculo el riesgo total")
def _compute_risk(mg_ctx: dict):
    from features.delta_tracking.application.task_synthesis import compute_total_risk

    mg_ctx["low_risk"] = compute_total_risk(mg_ctx["low_preds"])
    mg_ctx["high_risk"] = compute_total_risk(mg_ctx["high_preds"])


@then("el riesgo está entre 0 y 0.5")
def _risk_range(mg_ctx: dict):
    assert 0.0 < mg_ctx["low_risk"] <= 0.50


@then("con más predicates el riesgo aumenta")
def _risk_increases(mg_ctx: dict):
    assert mg_ctx["low_risk"] < mg_ctx["high_risk"]


@then("el riesgo nunca supera 0.5")
def _risk_cap(mg_ctx: dict):
    assert mg_ctx["high_risk"] <= 0.50


@given("listas de archivos y tareas con archivos impactados")
def _known_files_input(mg_ctx: dict):
    mg_ctx["known_input"] = [["src/a.py", "src/b.py"], "src/a.py"]
    mg_ctx["tasks_w_files"] = [{"impacted_artifacts": {"files": ["src/c.py"]}}]


@when("recolecto archivos conocidos")
def _collect_known(mg_ctx: dict):
    from features.delta_tracking.application.task_synthesis import (
        collect_known_files,
    )

    mg_ctx["known"] = collect_known_files(
        mg_ctx["known_input"], mg_ctx["tasks_w_files"]
    )


@then("obtengo la lista aplana sin duplicados")
def _known_dedup(mg_ctx: dict):
    assert mg_ctx["known"] == ["src/a.py", "src/b.py", "src/c.py"]


# ── 2. ceremonies (backlog WSJF + planning ceremony) ─────────────


def _make_backlog(tmp: str) -> str:
    from features.delta_tracking.application.ceremonies import cmd_backlog

    backlog_dir = f"{tmp}/delta"
    assert cmd_backlog(["init", backlog_dir]) == 0
    assert (
        cmd_backlog(
            [
                "add",
                backlog_dir,
                "--title",
                "Implementar login",
                "--value",
                "8",
                "--effort",
                "3",
                "--scrum-role",
                "po",
            ]
        )
        == 0
    )
    assert (
        cmd_backlog(
            [
                "add",
                backlog_dir,
                "--title",
                "Refactor del modulo core",
                "--value",
                "6",
                "--effort",
                "5",
            ]
        )
        == 0
    )
    return backlog_dir


@given("un backlog con dos items")
def _backlog_two_items(mg_ctx: dict, tmp_path):
    from features.delta_tracking.application.ceremonies import _load_backlog

    mg_ctx["backlog_dir"] = _make_backlog(str(tmp_path))
    mg_ctx["items"] = _load_backlog(mg_ctx["backlog_dir"])


@when("cargo el backlog")
def _load_backlog_step(mg_ctx: dict):
    from features.delta_tracking.application.ceremonies import _load_backlog

    mg_ctx["items"] = _load_backlog(mg_ctx["backlog_dir"])


@then('el primer item es "Implementar login" con mayor ratio WSJF')
def _wsjf_first(mg_ctx: dict):
    assert mg_ctx["items"][0]["title"] == "Implementar login"


@given("un proyecto con chain y backlog")
def _project_with_chain(mg_ctx: dict, tmp_path):
    from shared.libs.cognitive_graph import CognitiveGraph

    proj = str(tmp_path / "proj")
    data_dir = f"{proj}/data"
    import os

    os.makedirs(data_dir, exist_ok=True)
    with open(f"{data_dir}/project.json", "w") as f:
        json.dump({"project_id": "projX", "name": "Test"}, f)
    CognitiveGraph(proj, register_defaults=register_default_memory_indexes)
    mg_ctx["chain"] = f"{data_dir}/spring/chain-proj"
    mg_ctx["backlog_dir"] = _make_backlog(str(tmp_path))
    mg_ctx["tmp"] = str(tmp_path)


@when("ejecuto propose, refine y commit")
def _planning_flow(mg_ctx: dict):
    from features.delta_tracking.application.ceremonies import cmd_planning

    rc_propose = cmd_planning(
        [
            "propose",
            mg_ctx["chain"],
            "--project-id",
            "projX",
            "--goal",
            "Implementar el login del sistema",
            "--backlog-dir",
            mg_ctx["backlog_dir"],
            "--item-ids",
            "BL-001,BL-002",
        ]
    )
    mg_ctx["rc_propose"] = rc_propose

    task_json = json.dumps(
        {
            "task_id": "TASK-003",
            "type": "feature",
            "objective": "Implementar logout de sesion",
            "result_state": "El logout funciona correctamente",
            "domain": "auth",
            "impacted_artifacts": {"files": []},
            "risks": {"curvature": 0.2},
            "validation": {"predicates": []},
        }
    )
    mg_ctx["rc_refine"] = cmd_planning(
        ["refine", mg_ctx["chain"], "--edit", f"tasks:add={task_json}"]
    )
    mg_ctx["rc_commit"] = cmd_planning(["commit", mg_ctx["chain"]])


@then("el plan se propone con rc=3")
def _rc_propose(mg_ctx: dict):
    assert mg_ctx["rc_propose"] == 3


@then("el refine con edición de tareas devuelve rc=0")
def _rc_refine(mg_ctx: dict):
    assert mg_ctx["rc_refine"] == 0


@then("el commit devuelve rc=0")
def _rc_commit(mg_ctx: dict):
    assert mg_ctx["rc_commit"] == 0


@then("no existe plan.json (delta es la fuente única)")
def _no_plan_json(mg_ctx: dict):
    from pathlib import Path

    assert not Path(mg_ctx["chain"], "plan.json").exists()
    assert not Path(mg_ctx["chain"], "progress.json").exists()


@then("el spring existe en el delta")
def _spring_exists(mg_ctx: dict):
    from features.delta_tracking.application.ceremonies import _resolve_project_root
    from features.delta_tracking.application.services.spring_service import (
        SpringService,
    )

    project_root = _resolve_project_root("projX", mg_ctx["chain"])
    assert project_root
    spring_svc = SpringService(project_root)
    chain_id = mg_ctx["chain"].rsplit("/", 1)[-1]
    spring = spring_svc.ensure_spring_imported(mg_ctx["chain"], chain_id)
    mg_ctx["spring"] = spring
    mg_ctx["specs"] = spring_svc.get_specs_legacy(chain_id)
    assert spring is not None


@then("hay 3 specs en el delta")
def _three_specs(mg_ctx: dict):
    assert len(mg_ctx["specs"]) == 3


@then("todos los items del backlog están in-sprint")
def _items_insprint(mg_ctx: dict):
    from features.delta_tracking.application.ceremonies import _load_backlog

    items = _load_backlog(mg_ctx["backlog_dir"])
    assert all(it["status"] == "in-sprint" for it in items)


# ── 3. verify_engine (símbolos, archivos, comandos, cache) ───────


@given("un proyecto con un símbolo existente")
def _proj_symbol(mg_ctx: dict, tmp_path):
    _make_proj(tmp_path)
    mg_ctx["proj"] = str(tmp_path)


@given("un proyecto con símbolo existente")
def _proj_symbol_short(mg_ctx: dict, tmp_path):
    _make_proj(tmp_path)
    mg_ctx["proj"] = str(tmp_path)


@given("un símbolo que no existe")
def _symbol_absent(mg_ctx: dict):
    mg_ctx["ghost"] = "not_there"


@when("verifico la existencia de símbolos")
def _verify_symbols(mg_ctx: dict):
    from tasks.infrastructure.verify_engine import verify_predicate

    mg_ctx["res_ok"], mg_ctx["info_ok"] = verify_predicate(
        {"type": "symbol_exists", "symbol": "hello_world", "file": "src/foo.py"},
        mg_ctx["proj"],
    )
    mg_ctx["res_bad"], _ = verify_predicate(
        {
            "type": "symbol_exists",
            "symbol": mg_ctx["ghost"],
            "file": "src/foo.py",
        },
        mg_ctx["proj"],
    )


@then("el símbolo existente pasa")
def _symbol_passed(mg_ctx: dict):
    assert mg_ctx["res_ok"] and mg_ctx["info_ok"]["method"] == "symbol_exists"


@then("el símbolo ausente falla")
def _symbol_failed(mg_ctx: dict):
    assert not mg_ctx["res_bad"]


@given("un símbolo zombi")
def _symbol_zombie(mg_ctx: dict):
    mg_ctx["zombie"] = "zombie_symbol"


@when("verifico que el símbolo no exista")
def _verify_not_exists(mg_ctx: dict):
    from tasks.infrastructure.verify_engine import verify_predicate

    mg_ctx["res_zombie"], _ = verify_predicate(
        {"type": "symbol_not_exists", "symbol": mg_ctx["zombie"], "file": "src/foo.py"},
        mg_ctx["proj"],
    )
    mg_ctx["res_exists"], _ = verify_predicate(
        {"type": "symbol_not_exists", "symbol": "hello_world", "file": "src/foo.py"},
        mg_ctx["proj"],
    )


@then("el zombi pasa la verificación")
def _zombie_passed(mg_ctx: dict):
    assert mg_ctx["res_zombie"]


@then("el símbolo existente falla")
def _existing_failed(mg_ctx: dict):
    assert not mg_ctx["res_exists"]


@given("un proyecto con un archivo existente")
def _proj_file(mg_ctx: dict, tmp_path):
    _make_proj(tmp_path)
    mg_ctx["proj"] = str(tmp_path)


@given("un archivo faltante")
def _file_missing(mg_ctx: dict):
    mg_ctx["missing"] = "src/missing.py"


@when("verifico la existencia de archivos")
def _verify_files(mg_ctx: dict):
    from tasks.infrastructure.verify_engine import verify_predicate

    mg_ctx["file_ok"], _ = verify_predicate(
        {"type": "file_exists", "path": "src/foo.py"}, mg_ctx["proj"]
    )
    mg_ctx["file_bad"], _ = verify_predicate(
        {"type": "file_exists", "path": mg_ctx["missing"]}, mg_ctx["proj"]
    )


@then("el archivo existente pasa")
def _file_passed(mg_ctx: dict):
    assert mg_ctx["file_ok"]


@then("el archivo faltante falla")
def _file_failed(mg_ctx: dict):
    assert not mg_ctx["file_bad"]


@given("un proyecto en un directorio temporal")
def _proj_tmp(mg_ctx: dict, tmp_path):
    mg_ctx["proj"] = str(tmp_path)


@when('ejecuto un comando que contiene "42"')
def _cmd_output(mg_ctx: dict):
    from tasks.infrastructure.verify_engine import verify_predicate

    pred = {
        "type": "command_output",
        "command": 'python3 -c "print(42)"',
        "contains": "42",
    }
    mg_ctx["cmd_ok"], mg_ctx["cmd_info"] = verify_predicate(pred, mg_ctx["proj"])


@then('la verificación pasa con método "command_output"')
def _cmd_passed(mg_ctx: dict):
    assert mg_ctx["cmd_ok"] and mg_ctx["cmd_info"]["method"] == "command_output"


@then('si busco "NOPE" falla')
def _cmd_nope_fails(mg_ctx: dict):
    from tasks.infrastructure.verify_engine import verify_predicate

    ok2, _ = verify_predicate(
        {
            "type": "command_output",
            "command": 'python3 -c "print(42)"',
            "contains": "NOPE",
        },
        mg_ctx["proj"],
    )
    assert not ok2


@when("ejecuto una verificación manual")
def _manual_verify(mg_ctx: dict):
    from tasks.infrastructure.verify_engine import verify_predicate

    mg_ctx["manual_ok"], mg_ctx["manual_info"] = verify_predicate(
        {"type": "manual", "description": "revisar UX a mano"}, mg_ctx["proj"]
    )


@then('pasa con método "manual"')
def _manual_passed(mg_ctx: dict):
    assert mg_ctx["manual_ok"] and mg_ctx["manual_info"]["method"] == "manual"


@when("ejecuto la misma verificación dos veces")
def _cache_twice(mg_ctx: dict):
    from tasks.infrastructure.verify_engine import (
        invalidate_verify_cache,
        verify_predicate,
    )

    invalidate_verify_cache()
    pred = {
        "type": "symbol_exists",
        "symbol": "hello_world",
        "file": "src/foo.py",
    }
    mg_ctx["c1"] = verify_predicate(pred, mg_ctx["proj"])
    mg_ctx["c2"] = verify_predicate(pred, mg_ctx["proj"])


@then("ambos resultados son idénticos")
def _cache_identical(mg_ctx: dict):
    assert mg_ctx["c1"] == mg_ctx["c2"]


@given("un proyecto con tres predicates")
def _three_predicates(mg_ctx: dict, tmp_path):
    _make_proj(tmp_path)
    mg_ctx["proj"] = str(tmp_path)


@when("ejecuto el batch")
def _batch_run(mg_ctx: dict):
    from tasks.infrastructure.verify_engine import (
        invalidate_verify_cache,
        verify_predicates_batch,
    )

    invalidate_verify_cache()
    preds = [
        {"type": "symbol_exists", "symbol": "hello_world", "file": "src/foo.py"},
        {"type": "symbol_exists", "symbol": "Greeter", "file": "src/foo.py"},
        {"type": "symbol_exists", "symbol": "ghost", "file": "src/foo.py"},
    ]
    mg_ctx["batch"] = [r[0] for r in verify_predicates_batch(preds, mg_ctx["proj"])]


@then("los resultados mantienen el orden [True, True, False]")
def _batch_order(mg_ctx: dict):
    assert mg_ctx["batch"] == [True, True, False]


@given("un proyecto con dos predicates")
def _two_predicates(mg_ctx: dict, tmp_path):
    _make_proj(tmp_path)
    mg_ctx["proj"] = str(tmp_path)


@when("ejecuto verify_predicates como task")
def _verify_as_task(mg_ctx: dict):
    from features.delta_tracking.tasks.verification import verify_predicates
    from shared.common.shared_domain import ExecutionContext

    ctx = ExecutionContext.create(session_id="s", chain_dir=mg_ctx["proj"])
    mg_ctx["vr"] = verify_predicates(
        ctx,
        {
            "predicates": [
                {
                    "type": "symbol_exists",
                    "symbol": "hello_world",
                    "file": "src/foo.py",
                },
                {"type": "symbol_exists", "symbol": "ghost", "file": "src/foo.py"},
            ],
            "project_root": mg_ctx["proj"],
        },
    )


@then("la task pasa")
def _task_passed(mg_ctx: dict):
    assert mg_ctx["vr"].ok


@then("all_passed es False")
def _all_passed_false(mg_ctx: dict):
    assert mg_ctx["vr"].data["all_passed"] is False


@then("failed_count es 1")
def _failed_count(mg_ctx: dict):
    assert mg_ctx["vr"].data["failed_count"] == 1


@then("passed_count es 1")
def _passed_count(mg_ctx: dict):
    assert mg_ctx["vr"].data["passed_count"] == 1


@given("una sentencia en lenguaje natural")
def _nl_sentence(mg_ctx: dict):
    mg_ctx["nl"] = "la funcion debe retornar algo"


@given("una sentencia de shell")
def _shell_sentence(mg_ctx: dict):
    mg_ctx["shell"] = 'python3 -c "print(1)"'


@when("parseo ambos predicates")
def _parse_both(mg_ctx: dict):
    from tasks.infrastructure.verify_engine import (
        _is_string_a_shell_command,
        _parse_predicate,
    )

    mg_ctx["shell_detected"] = _is_string_a_shell_command(mg_ctx["shell"])
    mg_ctx["nl_detected"] = _is_string_a_shell_command(mg_ctx["nl"])
    mg_ctx["nl_parsed"] = _parse_predicate("def hello_world en src/foo.py")


@then("el lenguaje natural se clasifica como must_exist")
def _nl_must_exist(mg_ctx: dict):
    assert not mg_ctx["nl_detected"]
    assert mg_ctx["nl_parsed"][1] == "must_exist"
    assert mg_ctx["nl_parsed"][2] == "src/foo.py"


@then("la shell se clasifica como comando")
def _shell_command(mg_ctx: dict):
    assert mg_ctx["shell_detected"]


@given("un archivo de test que pasa")
def _test_passes(mg_ctx: dict, tmp_path):
    (tmp_path / "test_demo.py").write_text("def test_demo():\n    assert 1 == 1\n")
    mg_ctx["proj"] = str(tmp_path)


@given("un test inexistente")
def _test_missing(mg_ctx: dict):
    mg_ctx["ghost_test"] = "test_inexistente_xyz"


@when("ejecuto run_verification tipo test")
def _run_test_verification(mg_ctx: dict):
    from tasks.infrastructure.verify_engine import run_verification

    mg_ctx["test_ok"] = run_verification(
        {"predicate": "test_demo", "type": "test"}, mg_ctx["proj"]
    )
    mg_ctx["test_bad"] = run_verification(
        {"predicate": mg_ctx["ghost_test"], "type": "test"}, mg_ctx["proj"]
    )


@then("el test existente pasa")
def _test_existing_passed(mg_ctx: dict):
    assert mg_ctx["test_ok"] is True


@then("el inexistente falla")
def _test_ghost_failed(mg_ctx: dict):
    assert mg_ctx["test_bad"] is False


# ── 4. side_effect_contract (envelope estándar) ──────────────────


@given("un side effect de tipo index_code")
def _se_index_code(mg_ctx: dict):
    mg_ctx["se_type"] = "index_code"


@when("creo envelopes ok, failed y degraded")
def _make_envelopes(mg_ctx: dict):
    from shared.core.engine.domain.side_effect_contract import degraded, failed, ok

    mg_ctx["e_ok"] = ok(
        type=mg_ctx["se_type"],
        scope="code_index",
        summary="CPG indexado",
        signals={"quality_passed": True},
    )
    mg_ctx["e_failed"] = failed(
        type=mg_ctx["se_type"], scope="code_index", summary="falló"
    )
    mg_ctx["e_degraded"] = degraded(
        type=mg_ctx["se_type"], scope="code_index", summary="degradado"
    )


@then('ok tiene status "ok" y signals correctos')
def _envelope_ok(mg_ctx: dict):
    from shared.core.engine.domain.side_effect_contract import is_standard

    e = mg_ctx["e_ok"]
    assert is_standard(e)
    assert e["status"] == "ok"
    assert e["scope"] == "code_index"
    assert e["signals"]["quality_passed"] is True


@then('failed tiene status "failed"')
def _envelope_failed(mg_ctx: dict):
    assert mg_ctx["e_failed"]["status"] == "failed"


@then('degraded tiene status "degraded"')
def _envelope_degraded(mg_ctx: dict):
    assert mg_ctx["e_degraded"]["status"] == "degraded"


@given("un envelope con scope inexistente")
def _scope_invalid(mg_ctx: dict):
    mg_ctx["se_type"] = "x"


@when("creo el envelope ok")
def _make_ok(mg_ctx: dict):
    from shared.core.engine.domain.side_effect_contract import ok

    mg_ctx["e_ok"] = ok(
        type=mg_ctx["se_type"], scope="scope_inexistente", summary="s"
    )


@then("el scope se degrada a SCOPE_TRACKING")
def _scope_fallback(mg_ctx: dict):
    from shared.core.engine.domain.side_effect_contract import SCOPE_TRACKING

    assert mg_ctx["e_ok"]["scope"] == SCOPE_TRACKING


@given("un dict legacy con ok=True y files=5")
def _legacy_dict(mg_ctx: dict):
    mg_ctx["legacy"] = {"ok": True, "files": 5}


@when("normalizo al envelope estándar")
def _normalize_env(mg_ctx: dict):
    from shared.core.engine.domain.side_effect_contract import normalize

    mg_ctx["env"] = normalize(mg_ctx["legacy"], "index_code", "code_index")


@then('el envelope es estándar con status "ok"')
def _env_standard_ok(mg_ctx: dict):
    from shared.core.engine.domain.side_effect_contract import is_standard

    assert is_standard(mg_ctx["env"])
    assert mg_ctx["env"]["status"] == "ok"


@then("metadata contiene files=5")
def _env_metadata(mg_ctx: dict):
    assert mg_ctx["env"]["metadata"]["files"] == 5


@given("None como resultado")
def _none_result(mg_ctx: dict):
    mg_ctx["legacy"] = None


@then('el envelope tiene status "failed"')
def _env_none_failed(mg_ctx: dict):
    assert mg_ctx["env"]["status"] == "failed"


# ── 5. Orquestador: dispatch de quality_gate_failed ──────────────


class _FakeWorkflowRepo:
    def __init__(self, event_map: dict | None = None):
        self._event_map = event_map or {}

    def load(self, chain_dir):
        event_map = self._event_map

        class W:
            def find_event_handler(self, name):
                if name in event_map:
                    return {"transition": "needs_refine"}
                return None

        return W()


class _FakeStateRepo:
    def load(self, chain_dir):
        class S:
            current_node = "IMPLEMENT"
            current_sub_state = "WORK"

        return S()


class _FakeTracer:
    def trace(self, *a, **k):
        pass

    def trace_event(self, *a, **k):
        pass


class _FakeCtx:
    chain_dir = "/tmp/chain"
    task_id = "T1"
    session_id = "s1"
    trace_id = "t1"
    started_at = "2026-01-01"


@given("un OrchestrationService con repos falsos")
def _svc_fakes(mg_ctx: dict):
    from tests.conftest import make_orchestration_service

    # Camino A: registry mínimo explícito — el test reemplaza los repos por
    # fakes y solo prueba dispatch de side effects, no ejecuta tasks.
    svc = make_orchestration_service(real=False)
    svc._workflow_repo = _FakeWorkflowRepo()
    svc._state_repo = _FakeStateRepo()
    svc._tracer = _FakeTracer()
    mg_ctx["svc"] = svc


@given("un side effect con quality_passed=False")
def _se_quality_fail(mg_ctx: dict):
    from shared.core.engine.domain.side_effect_contract import ok

    mg_ctx["result"] = {
        "side_effects": [
            {
                "type": "index_code",
                "ok": True,
                "result": ok(
                    type="index_code",
                    scope="code_index",
                    summary="CPG indexado; cyclo falla",
                    signals={
                        "quality_passed": False,
                        "failed_gate_names": ["cyclomatic_complexity"],
                        "recommend": "refactorizar_primero",
                    },
                ),
            }
        ]
    }


@when("despacho eventos desde side effects")
def _dispatch(mg_ctx: dict):
    se = mg_ctx["result"]["side_effects"][0]
    signals = (se.get("result") or {}).get("signals", {})
    if signals.get("quality_passed") is False:
        mg_ctx["svc"]._workflow_repo = _FakeWorkflowRepo({"quality_gate_failed"})
    mg_ctx["count"] = mg_ctx["svc"]._dispatch_events_from_side_effects(
        _FakeCtx(), mg_ctx["result"]
    )


@then("se dispara 1 evento quality_gate_failed")
def _dispatched_one(mg_ctx: dict):
    assert mg_ctx["count"] == 1


@then("el resultado registra failed_gates y recommend")
def _result_registered(mg_ctx: dict):
    assert mg_ctx["result"]["quality"]["last_scan"]["failed_gates"] == [
        "cyclomatic_complexity"
    ]
    assert mg_ctx["result"]["quality"]["last_scan"]["recommend"] == "refactorizar_primero"


@given("un side effect con quality_passed=True")
def _se_quality_ok(mg_ctx: dict):
    from shared.core.engine.domain.side_effect_contract import ok

    mg_ctx["result"] = {
        "side_effects": [
            {
                "type": "index_code",
                "ok": True,
                "result": ok(
                    type="index_code",
                    scope="code_index",
                    summary="CPG indexado; calidad OK",
                    signals={"quality_passed": True, "recommend": "indexar_ok"},
                ),
            }
        ]
    }


@then("no se disparan eventos")
def _none_dispatched(mg_ctx: dict):
    assert mg_ctx["count"] == 0


# ── 6. Monadas: TaskResult / StepResult ──────────────────────────


@given("un TaskResult exitoso")
def _tr_success(mg_ctx: dict):
    from shared.common.shared_domain.models import TaskResult

    mg_ctx["tr"] = TaskResult.success({"a": 1})


@when("aplico map")
def _map_tr(mg_ctx: dict):
    if "tr" in mg_ctx:  # TaskResult scenario
        mg_ctx["mapped"] = mg_ctx["tr"].map(lambda d: {"a": d["a"] + 1})
    else:  # VO demo scenario (n="no-int")
        mg_ctx["mapped"] = mg_ctx["Demo"].from_dict({"n": "no-int"}).map(
            lambda vo: vo.n * 10
        )


@then("se transforma el dato")
def _mapped_ok(mg_ctx: dict):
    assert mg_ctx["mapped"].ok and mg_ctx["mapped"].data["a"] == 2


@given("un TaskResult fallido")
def _tr_failure(mg_ctx: dict):
    from shared.common.shared_domain.models import TaskResult

    mg_ctx["tr"] = TaskResult.failure("boom")


@then("se short-circuitea sin error")
def _short_circuit(mg_ctx: dict):
    assert not mg_ctx["mapped"].ok and mg_ctx["mapped"].error == "boom"


@given("un TaskResult exitoso con valor 2")
def _tr_two(mg_ctx: dict):
    from shared.common.shared_domain.models import TaskResult

    mg_ctx["tr"] = TaskResult.success(2)


@when("encadeno and_then con *3 y +1")
def _and_then(mg_ctx: dict):
    from shared.common.shared_domain.models import TaskResult

    mg_ctx["chained"] = (
        mg_ctx["tr"]
        .and_then(lambda x: TaskResult.success(x * 3))
        .and_then(lambda x: TaskResult.success(x + 1))
    )


@then("obtengo 7")
def _seven(mg_ctx: dict):
    assert mg_ctx["chained"].ok and mg_ctx["chained"].data == 7


@given("un TaskResult exitoso con valor 1")
def _tr_one(mg_ctx: dict):
    from shared.common.shared_domain.models import TaskResult

    mg_ctx["tr"] = TaskResult.success(1)
    mg_ctx["calls"] = []


@when("encadeno un and_then que falla")
def _and_then_fail(mg_ctx: dict):
    from shared.common.shared_domain.models import TaskResult

    mg_ctx["chained"] = (
        mg_ctx["tr"]
        .and_then(lambda x: TaskResult.failure("frenó"))
        .and_then(lambda x: mg_ctx["calls"].append(x))
    )


@then("el siguiente and_then no se ejecuta")
def _and_then_skipped(mg_ctx: dict):
    assert not mg_ctx["chained"].ok and mg_ctx["calls"] == []


@when("aplico tap para observar")
def _tap_tr(mg_ctx: dict):
    from shared.common.shared_domain.models import TaskResult

    seen = []
    mg_ctx["seen"] = seen
    mg_ctx["tapped"] = TaskResult.success(5).tap(lambda d: seen.append(d))


@then("el valor permanece")
def _tap_kept(mg_ctx: dict):
    assert mg_ctx["tapped"].data == 5 and mg_ctx["seen"] == [5]


@when("aplico recover")
def _recover_tr(mg_ctx: dict):
    mg_ctx["recovered"] = mg_ctx["tr"].recover(lambda e: "respaldo")


@then("obtengo un valor de respaldo")
def _recover_value(mg_ctx: dict):
    assert mg_ctx["recovered"].ok and mg_ctx["recovered"].data == "respaldo"


@when("creo un StepResult con ese side effect")
def _make_step_result(mg_ctx: dict):
    from shared.core.engine.domain.step_result import StepResult
    from shared.common.shared_domain.models import TaskResult

    se = mg_ctx["result"]["side_effects"][0]
    sr = StepResult.success(
        task=TaskResult.success({"done": True}),
        side_effects=[se],
        advanced_to="CODE_REVIEW_SCAN",
        sub_state="WORK",
    )
    mg_ctx["sr"] = sr
    mg_ctx["q"] = sr.quality()
    mg_ctx["pd"] = sr.to_prompt_dict()


@then("quality() reporta quality_passed=False")
def _quality_false(mg_ctx: dict):
    assert mg_ctx["q"]["quality_passed"] is False


@then("failed_gates incluye cyclomatic_complexity")
def _q_gates(mg_ctx: dict):
    assert "cyclomatic_complexity" in mg_ctx["q"]["failed_gates"]


@then("to_prompt_dict expone step_failed_gates")
def _prompt_dict(mg_ctx: dict):
    assert mg_ctx["pd"]["step_quality_passed"] is False
    assert mg_ctx["pd"]["step_failed_gates"] == "cyclomatic_complexity"
    assert mg_ctx["pd"]["step_advanced_to"] == "CODE_REVIEW_SCAN"


@given("sub_state_data con campos del paso previo")
def _ssd_fields(mg_ctx: dict):
    mg_ctx["ssd"] = {
        "WORK": {
            "fields": {
                "prompt_placeholders": {
                    "step_ok": True,
                    "step_quality_passed": False,
                    "step_recommend": "refactorizar_primero",
                    "step_failed_gates": "cyclomatic_complexity",
                }
            }
        }
    }


@when("leo el último step result")
def _read_last(mg_ctx: dict):
    from shared.core.engine.application.prompt_context_builder import (
        _read_last_step_result,
    )

    mg_ctx["last"] = _read_last_step_result(mg_ctx["ssd"])


@then("obtengo los campos correctos")
def _last_fields(mg_ctx: dict):
    assert mg_ctx["last"]["step_quality_passed"] is False
    assert mg_ctx["last"]["step_recommend"] == "refactorizar_primero"


@given("sub_state_data vacío")
def _ssd_empty(mg_ctx: dict):
    mg_ctx["ssd"] = {}


@then("obtengo dict vacío")
def _last_empty_short(mg_ctx: dict):
    assert mg_ctx["last"] == {}


@then("obtengo un dict vacío")
def _last_empty(mg_ctx: dict):
    assert mg_ctx["last"] == {}


# ── 7. Templates de nodo: placeholders de paso ───────────────────


def _load_template(prompt_id: str) -> dict:
    import tempfile

    from features.main.prompts.loader import TemplateLoader

    with tempfile.TemporaryDirectory() as tmpdir:
        text = TemplateLoader(tmpdir).load(prompt_id)
    return json.loads(text) if text.lstrip().startswith("{") else {"template": text}


@given("el template implement/work")
def _tmpl_work(mg_ctx: dict):
    mg_ctx["t_work"] = _load_template("implement/work")


@given("el template implement/code-review-scan")
def _tmpl_scan(mg_ctx: dict):
    mg_ctx["t_scan"] = _load_template("implement/code-review-scan")


@given("el template refine/diagnose")
def _tmpl_diagnose(mg_ctx: dict):
    mg_ctx["t_diag"] = _load_template("refine/diagnose")


@then("implement/work tiene {step_quality_summary}, {step_failed_gates} y {step_recommend}")
def _tmpl_work_placeholders(mg_ctx: dict):
    tmpl = mg_ctx["t_work"]["template"]
    assert "{step_quality_summary}" in tmpl
    assert "{step_failed_gates}" in tmpl
    assert "{step_recommend}" in tmpl


@then("implement/code-review-scan tiene {step_quality_passed} y {step_failed_gates}")
def _tmpl_scan_placeholders(mg_ctx: dict):
    tmpl = mg_ctx["t_scan"]["template"]
    assert "{step_quality_passed}" in tmpl
    assert "{step_failed_gates}" in tmpl


@then("refine/diagnose tiene {step_quality_summary} y {step_recommend}")
def _tmpl_diag_placeholders(mg_ctx: dict):
    tmpl = mg_ctx["t_diag"]["template"]
    assert "{step_quality_summary}" in tmpl
    assert "{step_recommend}" in tmpl


@given("datos de calidad fallida")
def _quality_fail_data(mg_ctx: dict):
    mg_ctx["render_data"] = {
        "step_quality_summary": "CPG indexado; cyclo falla",
        "step_quality_passed": "False",
        "step_failed_gates": "cyclomatic_complexity",
        "step_recommend": "refactorizar_primero",
        "failed_gates": "cyclomatic_complexity",
    }


@when("renderizo cada template con datos")
def _render_with_data(mg_ctx: dict):
    from features.main.prompts.renderer import TemplateRenderer

    r = TemplateRenderer()
    mg_ctx["rendered"] = {
        pid: r.render(_load_template(pid)["template"], mg_ctx["render_data"])
        for pid in (
            "implement/work",
            "implement/code-review-scan",
            "refine/diagnose",
        )
    }


@then("cyclomatic_complexity aparece en el render")
def _render_contains(mg_ctx: dict):
    for out in mg_ctx["rendered"].values():
        assert "cyclomatic_complexity" in out


@when("renderizo sin datos")
def _render_empty(mg_ctx: dict):
    from features.main.prompts.renderer import TemplateRenderer

    r = TemplateRenderer()
    mg_ctx["rendered_empty"] = {
        pid: r.render(_load_template(pid)["template"], {})
        for pid in (
            "implement/work",
            "implement/code-review-scan",
            "refine/diagnose",
        )
    }


@then("no quedan placeholders sin resolver")
def _render_no_broken(mg_ctx: dict):
    for out in mg_ctx["rendered_empty"].values():
        assert "{step_" not in out


# ── 8. as_side_effect / emit_event ───────────────────────────────


@given("una función decorada con scope knowledge")
def _fn_knowledge(mg_ctx: dict):
    from shared.core.engine.domain.side_effect_contract import (
        SCOPE_KNOWLEDGE,
        as_side_effect,
    )

    @as_side_effect(scope=SCOPE_KNOWLEDGE)
    def fake_side(ctx, params):
        return {"ok": True, "nodes_saved": 5, "result": {"x": 1}}

    mg_ctx["fn"] = fake_side


@when("la ejecuto con contexto")
def _run_side(mg_ctx: dict):
    class Ctx:
        chain_dir = "/tmp"
        task_id = "T"
        session_id = "s"

    mg_ctx["env"] = mg_ctx["fn"](Ctx(), {})


@then("el envelope es estándar con scope SCOPE_KNOWLEDGE")
def _env_knowledge_scope(mg_ctx: dict):
    from shared.core.engine.domain.side_effect_contract import (
        SCOPE_KNOWLEDGE,
        is_standard,
    )

    assert is_standard(mg_ctx["env"])
    assert mg_ctx["env"]["scope"] == SCOPE_KNOWLEDGE
    assert mg_ctx["env"]["status"] == "ok"


@then("campos legacy y signals son consistentes")
def _legacy_signals(mg_ctx: dict):
    assert mg_ctx["env"].get("nodes_saved") == 5
    assert mg_ctx["env"]["signals"]["nodes_saved"] == 5


@given("una función decorada con scope decision")
def _fn_decision(mg_ctx: dict):
    from shared.core.engine.domain.side_effect_contract import (
        SCOPE_DECISION,
        as_side_effect,
    )

    @as_side_effect(scope=SCOPE_DECISION)
    def capture(ctx, params):
        return {"ok": True, "captured": True, "decision": "approve"}

    mg_ctx["fn"] = capture


@then("el envelope es estándar con scope SCOPE_DECISION")
def _env_decision_scope(mg_ctx: dict):
    from shared.core.engine.domain.side_effect_contract import (
        SCOPE_DECISION,
        is_standard,
    )

    assert is_standard(mg_ctx["env"])
    assert mg_ctx["env"]["scope"] == SCOPE_DECISION
    assert mg_ctx["env"]["signals"]["captured"] is True


@then('signals contienen decision="approve"')
def _decision_signal(mg_ctx: dict):
    assert mg_ctx["env"]["signals"]["decision"] == "approve"


@given("una función emit_event")
def _fn_emit(mg_ctx: dict):
    from tasks.side_effects.events import emit_event

    mg_ctx["fn"] = emit_event


@when("la ejecuto con evento qa_failed")
def _run_emit(mg_ctx: dict):
    class Ctx:
        chain_dir = "/tmp"
        task_id = "T1"
        trace_id = "t1"

    mg_ctx["env"] = mg_ctx["fn"](Ctx(), {"event": "qa_failed", "payload": {"x": 1}})


@then('el envelope tiene scope "event"')
def _env_event_scope(mg_ctx: dict):
    assert mg_ctx["env"]["scope"] == "event"
    assert mg_ctx["env"]["signals"]["event"] == "qa_failed"


@then('status es "ok"')
def _env_status_ok(mg_ctx: dict):
    assert mg_ctx["env"]["status"] == "ok"


@given("un side effect emit_event")
def _se_emit(mg_ctx: dict):
    from tasks.side_effects.events import emit_event

    mg_ctx["result"] = {
        "side_effects": [
            {"type": "emit_event", "ok": True, "result": emit_event(_FakeCtx(), {"event": "qa_failed"})}
        ]
    }
    mg_ctx["svc"]._workflow_repo = _FakeWorkflowRepo({"qa_failed"})


@then("se dispara 1 evento")
def _emit_dispatched(mg_ctx: dict):
    assert mg_ctx["count"] == 1


# ── 9. VOs tipados (from_dict + validación) ──────────────────────


@given("un VO TaskInput con files como lista")
def _vo_files_list(mg_ctx: dict):
    from dataclasses import dataclass

    from shared.common.shared_domain.validated_vo import TaskInput

    @dataclass(frozen=True)
    class Demo(TaskInput):
        files: list | None = None

        def _validate(self) -> str:
            if self.files is not None and not isinstance(self.files, list):
                return "files debe ser lista"
            return ""

    mg_ctx["Demo"] = Demo


@when("creo desde dict válido")
def _vo_from_valid(mg_ctx: dict):
    mg_ctx["vo"] = mg_ctx["Demo"].from_dict({"files": ["a.py"]})


@then("es exitoso con files correctos")
def _vo_ok(mg_ctx: dict):
    assert mg_ctx["vo"].ok and mg_ctx["vo"].data.files == ["a.py"]


@given("un VO TaskInput con files como string")
def _vo_files_str(mg_ctx: dict):
    from dataclasses import dataclass

    from shared.common.shared_domain.validated_vo import TaskInput

    @dataclass(frozen=True)
    class Demo(TaskInput):
        files: list | None = None

        def _validate(self) -> str:
            if self.files is not None and not isinstance(self.files, list):
                return "files debe ser lista"
            return ""

    mg_ctx["Demo"] = Demo


@when("creo desde dict inválido")
def _vo_from_invalid(mg_ctx: dict):
    mg_ctx["vo"] = mg_ctx["Demo"].from_dict({"files": "nope"})


@then("falla con el mensaje de validación")
def _vo_bad(mg_ctx: dict):
    assert not mg_ctx["vo"].ok and "files debe ser lista" in (mg_ctx["vo"].error or "")


@given("un VO TaskInput con dict None")
def _vo_none(mg_ctx: dict):
    from dataclasses import dataclass

    from shared.common.shared_domain.validated_vo import TaskInput

    @dataclass(frozen=True)
    class Demo(TaskInput):
        files: list | None = None

    mg_ctx["Demo"] = Demo


@when("creo desde dict None")
def _vo_from_none(mg_ctx: dict):
    mg_ctx["vo"] = mg_ctx["Demo"].from_dict(None)


@then("es exitoso")
def _vo_ok_generic(mg_ctx: dict):
    assert mg_ctx["vo"].ok


@given("un VO Demo con n=2")
def _demo_n_two(mg_ctx: dict):
    from dataclasses import dataclass

    from shared.common.shared_domain.validated_vo import TaskInput

    @dataclass(frozen=True)
    class Demo(TaskInput):
        n: int = 0

    mg_ctx["Demo"] = Demo


@when("aplico map multiplicando por 10")
def _vo_map10(mg_ctx: dict):
    mg_ctx["mapped"] = mg_ctx["Demo"].from_dict({"n": 2}).map(lambda vo: vo.n * 10)


@then("obtengo 20")
def _vo_twenty(mg_ctx: dict):
    assert mg_ctx["mapped"].ok and mg_ctx["mapped"].data == 20


@given('un VO Demo con n="no-int"')
def _demo_n_bad(mg_ctx: dict):
    from dataclasses import dataclass

    from shared.common.shared_domain.validated_vo import TaskInput

    @dataclass(frozen=True)
    class Demo(TaskInput):
        n: int = 0

    mg_ctx["Demo"] = Demo


@then("no crashea")
def _vo_no_crash(mg_ctx: dict):
    assert mg_ctx["mapped"].ok


@given("ReviewInput con files y thresholds")
def _review_input_ok(mg_ctx: dict):
    from features.delta_tracking.tasks.review import ReviewInput

    mg_ctx["vo"] = ReviewInput.from_dict(
        {"files": ["a.py"], "thresholds": {"max_cyclo": 10}}
    )


@given("ReviewInput con files como string")
def _review_input_bad(mg_ctx: dict):
    from features.delta_tracking.tasks.review import ReviewInput

    mg_ctx["vo"] = ReviewInput.from_dict({"files": "nope"})


@given("VerifyInput con predicates normalizados")
def _verify_input_norm(mg_ctx: dict):
    from features.delta_tracking.tasks.verification import VerifyInput

    mg_ctx["vo"] = VerifyInput.from_dict(
        {"predicates": [{"predicate": "def x", "type": "symbol_exists"}]}
    )
    mg_ctx["normalized"] = None


@then("es exitoso")
def _vo_v_ok(mg_ctx: dict):
    assert mg_ctx["vo"].ok


@then("normalized_predicates devuelve la lista")
def _verify_normalized(mg_ctx: dict):
    assert mg_ctx["vo"].data.normalized_predicates() == ["def x"]


@then("falla")
def _vo_falla(mg_ctx: dict):
    target = mg_ctx.get("cp_res") or mg_ctx.get("vo")
    assert target is not None
    assert not target.ok


@given("VerifyInput con predicate único")
def _verify_input_single(mg_ctx: dict):
    from features.delta_tracking.tasks.verification import VerifyInput

    mg_ctx["vo"] = VerifyInput.from_dict({"predicate": "def y"})


@then("normalized_predicates devuelve el string")
def _verify_single(mg_ctx: dict):
    assert mg_ctx["vo"].data.normalized_predicates() == ["def y"]


@given("VerifyInput vacío")
def _verify_input_empty(mg_ctx: dict):
    from features.delta_tracking.tasks.verification import VerifyInput

    mg_ctx["vo"] = VerifyInput.from_dict({})


@given("ImplementInput con objective y files")
def _impl_input_ok(mg_ctx: dict):
    from features.delta_tracking.tasks.implementation import ImplementInput

    mg_ctx["vo"] = ImplementInput.from_dict({"objective": "X", "files": ["a.py"]})


@given("ImplementInput con files como string")
def _impl_input_bad(mg_ctx: dict):
    from features.delta_tracking.tasks.implementation import ImplementInput

    mg_ctx["vo"] = ImplementInput.from_dict({"files": "nope"})


@given("CaptureTaskInput vacío")
def _capture_empty(mg_ctx: dict):
    from tasks.actions.capture import CaptureTaskInput

    mg_ctx["vo"] = CaptureTaskInput.from_dict({})


@then("falla por falta de objective")
def _capture_fails(mg_ctx: dict):
    assert mg_ctx["vo"].error


@given("CaptureTaskInput con objective y files")
def _capture_ok(mg_ctx: dict):
    from tasks.actions.capture import CaptureTaskInput

    mg_ctx["vo"] = CaptureTaskInput.from_dict(
        {"objective": "implementar X", "files": ["a.py"]}
    )


@given("ResearchInput vacío")
def _research_empty(mg_ctx: dict):
    from tasks.actions.research import ResearchInput

    mg_ctx["vo"] = ResearchInput.from_dict({})


@given("ResearchInput con topic")
def _research_ok(mg_ctx: dict):
    from tasks.actions.research import ResearchInput

    mg_ctx["vo"] = ResearchInput.from_dict({"topic": "vector dbs"})


@given("KgExtractInput con scope functions")
def _kg_extract_ok(mg_ctx: dict):
    from tasks.actions.extract_knowledge import KgExtractInput

    mg_ctx["vo"] = KgExtractInput.from_dict({"scope": "functions"})


@given("KgExtractInput con scope bogus")
def _kg_extract_bad(mg_ctx: dict):
    from tasks.actions.extract_knowledge import KgExtractInput

    mg_ctx["vo"] = KgExtractInput.from_dict({"scope": "bogus"})


@given("ProjectInitInput vacío")
def _project_init_empty(mg_ctx: dict):
    from tasks.actions.initialize_project import ProjectInitInput

    mg_ctx["vo"] = ProjectInitInput.from_dict({})


@then("falla por falta de project_id")
def _project_init_fails(mg_ctx: dict):
    assert not mg_ctx["vo"].ok


@given("ProjectInitInput con project_id y language")
def _project_init_ok(mg_ctx: dict):
    from tasks.actions.initialize_project import ProjectInitInput

    vo = ProjectInitInput.from_dict({"project_id": "p1", "language": "go"})
    assert vo.ok and vo.data.language == "go"
    mg_ctx["vo"] = vo


# ── 10. Checkpoint roundtrip y _read_last_step_result ─────────────


@given("un StepResult con quality_passed=False")
def _sr_quality_false(mg_ctx: dict):
    from shared.core.engine.domain.side_effect_contract import ok
    from shared.core.engine.domain.step_result import StepResult

    se = {
        "type": "index_code",
        "ok": True,
        "result": ok(
            type="index_code",
            scope="code_index",
            summary="CPG indexado; cyclo falla",
            signals={
                "quality_passed": False,
                "failed_gate_names": ["cyclomatic_complexity"],
                "recommend": "refactorizar_primero",
            },
        ),
    }
    mg_ctx["sr"] = StepResult.success(
        side_effects=[se], advanced_to="CODE_REVIEW_SCAN", sub_state="WORK"
    )


@when("hago roundtrip JSON")
def _checkpoint_roundtrip(mg_ctx: dict):
    from shared.core.engine.domain.step_result import StepResult

    cp = json.loads(json.dumps(mg_ctx["sr"].to_checkpoint()))
    restored = StepResult.from_checkpoint(cp)
    assert restored.ok
    mg_ctx["restored"] = restored.data


@then("se restaura correctamente")
def _restored_ok(mg_ctx: dict):
    assert mg_ctx["restored"].advanced_to == "CODE_REVIEW_SCAN"


@then("quality_passed sigue siendo booleano False")
def _restored_bool(mg_ctx: dict):
    qp = mg_ctx["restored"].signals["quality"]["quality_passed"]
    assert qp is False and isinstance(qp, bool)


@given("un checkpoint con ok=\"no-bool\"")
def _cp_bad(mg_ctx: dict):
    mg_ctx["cp"] = {"__checkpoint__": "StepResult:v1", "ok": "no-bool"}


@when("restauro desde checkpoint")
def _restore_cp(mg_ctx: dict):
    from shared.core.engine.domain.step_result import StepResult

    mg_ctx["cp_res"] = StepResult.from_checkpoint(mg_ctx["cp"])


@then('falla con error mencionando "ok"')
def _cp_error_ok(mg_ctx: dict):
    assert not mg_ctx["cp_res"].ok
    assert "ok" in (mg_ctx["cp_res"].error or "")


@given("un checkpoint sin marker")
def _cp_no_marker(mg_ctx: dict):
    mg_ctx["cp"] = {"ok": True}


@given("un StepResult con gates fallidas y recommend")
def _sr_gates(mg_ctx: dict):
    from shared.core.engine.domain.side_effect_contract import ok
    from shared.core.engine.domain.step_result import StepResult

    se = {
        "type": "index_code",
        "ok": True,
        "result": ok(
            type="index_code",
            scope="code_index",
            summary="CPG indexado; cyclo falla",
            signals={
                "quality_passed": False,
                "failed_gate_names": ["cyclomatic_complexity", "dead_code"],
                "recommend": "refactorizar_primero",
            },
        ),
    }
    mg_ctx["sr"] = StepResult.success(
        side_effects=[se], advanced_to="CODE_REVIEW_SCAN", sub_state="WORK"
    )


@when("serializo a checkpoint y lo leo como last_step_result")
def _cp_to_last(mg_ctx: dict):
    from shared.core.engine.application.prompt_context_builder import (
        _read_last_step_result,
    )

    cp = json.loads(json.dumps(mg_ctx["sr"].to_checkpoint()))
    ssd = {"WORK": {"fields": {"step_checkpoint": cp}}}
    mg_ctx["last"] = _read_last_step_result(ssd)


@then("obtengo quality_passed=False")
def _last_quality_false(mg_ctx: dict):
    assert mg_ctx["last"]["step_quality_passed"] is False


@then("failed_gates incluye cyclomatic_complexity y dead_code")
def _last_gates(mg_ctx: dict):
    assert mg_ctx["last"]["step_failed_gates"] == "cyclomatic_complexity, dead_code"


@then("advanced_to es CODE_REVIEW_SCAN")
def _last_advanced(mg_ctx: dict):
    assert mg_ctx["last"]["step_advanced_to"] == "CODE_REVIEW_SCAN"


@given("un checkpoint corrupto")
def _cp_corrupt(mg_ctx: dict):
    mg_ctx["cp"] = {"__checkpoint__": "StepResult:v1", "ok": "x"}


@when("lo leo como last_step_result")
def _cp_corrupt_read(mg_ctx: dict):
    from shared.core.engine.application.prompt_context_builder import (
        _read_last_step_result,
    )

    ssd = {"WORK": {"fields": {"step_checkpoint": mg_ctx["cp"]}}}
    mg_ctx["last"] = _read_last_step_result(ssd)


# ── 11. MCP config (bootstrap portable) ───────────────────────────


@given("el directorio del proyecto")
def _walker_dir(mg_ctx: dict):
    mg_ctx["walker_root"] = None


@when("busco _walker_root")
def _find_root(mg_ctx: dict):
    from protocols.cli.cli_interface.application.mcp_config_service import _walker_root

    mg_ctx["walker_root"] = _walker_root()


@then("existe features/cli_interface/mcp.py en el root")
def _root_has_mcp(mg_ctx: dict):
    import os

    assert os.path.exists(
        os.path.join(mg_ctx["walker_root"], "features", "cli_interface", "mcp.py")
    )


@when("genero mcp_servers_block")
def _mcp_block(mg_ctx: dict):
    from protocols.cli.cli_interface.application.mcp_config_service import (
        mcp_servers_block,
    )

    mg_ctx["mcp"] = mcp_servers_block()


@then("el servidor walker usa comando python3")
def _walker_python3(mg_ctx: dict):
    assert mg_ctx["mcp"]["mcpServers"]["walker"]["command"] == "python3"


@then('args son ["-m", "protocols.cli.cli_interface.mcp"]')
def _walker_args(mg_ctx: dict):
    assert mg_ctx["mcp"]["mcpServers"]["walker"]["args"] == [
        "-m",
        "protocols.cli.cli_interface.mcp",
    ]


@then("PYTHONPATH incluye el root del walker")
def _walker_pythonpath(mg_ctx: dict):
    from protocols.cli.cli_interface.application.mcp_config_service import _walker_root

    root = _walker_root()
    assert root in mg_ctx["mcp"]["mcpServers"]["walker"]["env"]["PYTHONPATH"]


@when("genero la configuración MCP")
def _gen_mcp(mg_ctx: dict):
    from protocols.cli.cli_interface.application.mcp_config_service import generate

    mg_ctx["mcp"] = generate()


@then("el bloque incluye mcpServers con walker")
def _mcp_servers_walker(mg_ctx: dict):
    assert "mcpServers" in mg_ctx["mcp"]
    assert "walker" in mg_ctx["mcp"]["mcpServers"]


@when("ejecuto cmd_mcp_config --raw")
def _cmd_mcp_raw(mg_ctx: dict):
    import contextlib
    import io

    from protocols.cli.cli_interface.application.mcp_config_service import cmd_mcp_config

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mg_ctx["rc"] = cmd_mcp_config(["--raw"])
    mg_ctx["raw_out"] = buf.getvalue().strip()


@then("el código de salida es 0")
def _rc_zero(mg_ctx: dict):
    assert mg_ctx["rc"] == 0


@then('el JSON incluye "walker" con command="python3"')
def _raw_json(mg_ctx: dict):
    parsed = json.loads(mg_ctx["raw_out"])
    assert "walker" in parsed
    assert parsed["walker"]["command"] == "python3"


@when("llamo _walker_root y mcp_servers_block")
def _service_both(mg_ctx: dict):
    from protocols.cli.cli_interface.application.mcp_config_service import (
        _walker_root,
        mcp_servers_block,
    )

    mg_ctx["walker_root"] = _walker_root()
    mg_ctx["mcp"] = mcp_servers_block()


@then("el root existe con mcp.py")
def _root_exists(mg_ctx: dict):
    import os

    assert os.path.exists(
        os.path.join(mg_ctx["walker_root"], "features", "cli_interface", "mcp.py")
    )


@then("el servidor walker tiene la configuración esperada")
def _walker_cfg_expected(mg_ctx: dict):
    server = mg_ctx["mcp"]["mcpServers"]["walker"]
    assert server["command"] == "python3"
    assert server["args"] == ["-m", "protocols.cli.cli_interface.mcp"]
    assert mg_ctx["walker_root"] in server["env"]["PYTHONPATH"]


@then("el JSON es serializable")
def _json_serializable(mg_ctx: dict):
    json.dumps(mg_ctx["mcp"])


@given("una Request vacía")
def _request_empty(mg_ctx: dict):
    from protocols.cli.cli_interface.infrastructure.contracts import Request

    mg_ctx["req"] = Request(params={})


@when("invoco handle_mcp_config")
def _handle_mcp_config(mg_ctx: dict):
    from protocols.cli.cli_interface.application.handlers import handle_mcp_config

    mg_ctx["resp"] = handle_mcp_config(mg_ctx["req"])


@then('el tipo es "mcp_config"')
def _resp_type_mcp(mg_ctx: dict):
    assert mg_ctx["resp"].type == "mcp_config"


@then("incluye mcpServers con walker")
def _resp_mcp_servers(mg_ctx: dict):
    assert "mcpServers" in mg_ctx["resp"].data


@then('walker tiene command="python3"')
def _resp_walker_cmd(mg_ctx: dict):
    assert mg_ctx["resp"].data["mcpServers"]["walker"]["command"] == "python3"


@when("invoco con raw=true")
def _handle_mcp_raw(mg_ctx: dict):
    from protocols.cli.cli_interface.application.handlers import handle_mcp_config
    from protocols.cli.cli_interface.infrastructure.contracts import Request

    mg_ctx["resp"] = handle_mcp_config(Request(params={"raw": "true"}))


@then("incluye solo el bloque walker")
def _resp_raw_only(mg_ctx: dict):
    assert "mcpServers" not in mg_ctx["resp"].data
    assert "walker" in mg_ctx["resp"].data


@when("invoco handle_help")
def _handle_help(mg_ctx: dict):
    from protocols.cli.cli_interface.application.handlers import handle_help

    mg_ctx["resp"] = handle_help(mg_ctx["req"])


@then('el tipo es "help"')
def _resp_help_type(mg_ctx: dict):
    assert mg_ctx["resp"].type == "help"


@then('los comandos incluyen "mcp-config"')
def _help_mcp_config(mg_ctx: dict):
    names = [c["name"] for c in mg_ctx["resp"].data.get("commands", [])]
    assert "mcp-config" in names


@then("el usage no bifurca por formato de agente")
def _help_no_format(mg_ctx: dict):
    names = {c["name"]: c for c in mg_ctx["resp"].data.get("commands", [])}
    usage = names["mcp-config"]["usage"]
    assert "format=" not in usage
    assert "mcp-config" in usage


@given("el router de CLI")
def _router(mg_ctx: dict):
    import protocols.cli.cli_interface as ci

    mg_ctx["router"] = ci._ROUTER


@when("listo las rutas")
def _list_routes(mg_ctx: dict):
    mg_ctx["routes"] = mg_ctx["router"].list_routes()


@then('"mcp-config" está en los patrones')
def _route_mcp(mg_ctx: dict):
    patterns = [r.get("pattern", "") for r in mg_ctx["routes"]]
    assert "mcp-config" in patterns


@when('resuelvo "walker/session-20260731/status"')
def _resolve_status(mg_ctx: dict):
    mg_ctx["resolved"] = mg_ctx["router"].resolve(
        "walker/session-20260731/status", {}, "GET"
    )


@then("el handler existe")
def _handler_exists(mg_ctx: dict):
    assert mg_ctx["resolved"][0] is not None


@then('projectId es "walker"')
def _project_id(mg_ctx: dict):
    assert mg_ctx["resolved"][1].get("projectId") == "walker"


@then('sprintId es "session-20260731"')
def _sprint_id_route(mg_ctx: dict):
    assert mg_ctx["resolved"][1].get("sprintId") == "session-20260731"


@when("creo Request con params de ruta")
def _req_params(mg_ctx: dict):
    from protocols.cli.cli_interface.infrastructure.contracts import Request

    mg_ctx["req"] = Request(params=mg_ctx["resolved"][1], flags={})


@then('sprint_id es "session-20260731"')
def _req_sprint_id(mg_ctx: dict):
    assert mg_ctx["req"].sprint_id == "session-20260731"


@when("creo Request con flags sprint_id")
def _req_flags(mg_ctx: dict):
    from protocols.cli.cli_interface.infrastructure.contracts import Request

    mg_ctx["req"] = Request(params={}, flags={"sprint_id": "session-20260804"})


@then('sprint_id es "session-20260804"')
def _req_sprint_id_2(mg_ctx: dict):
    assert mg_ctx["req"].sprint_id == "session-20260804"