"""tests/test_registry_completeness_bdd.py — Completitud del TaskRegistry (AGENTS.md).

Patrón obligatorio del AGENTS.md: todo registry construido por bootstrap() o
build_*() debe tener un test de completitud que valide el conjunto esperado
de componentes. Aquí validamos que bootstrap_tasks()/bootstrap() registran
las tasks reales del workflow — la rotura de composición (registry vacío)
debe ser visible en CI, no enmascarada por register_mock_tasks().
"""
from __future__ import annotations


EXPECTED_WORKFLOW_TASKS = {
    "plan_sprint",          # SPRINT.PLANNING (planning.py → PlanSprintUseCase)
    "spec_task",            # SPRINT.TASK_SPEC (planning.py → SpecifySpecUseCase)
    "review_code",          # IMPLEMENT.CODE_REVIEW_SCAN (review.py → CPG port)
    "close_sprint",         # SPRINT.CLOSE (planning.py → CloseSprintUseCase)
    "implement_task",       # IMPLEMENT.WORK (implementation.py)
    "run_tests",            # VERIFY.QA_RUN (implementation.py)
    "verify_predicates",    # VERIFY.PREDICATE_CHECK (verification.py)
    "scan_code_and_build_cpg",  # PROJECT_INIT (project_init.py)
    "build_initial_ckg",        # PROJECT_INIT (project_init.py)
    "create_first_sprint",      # PROJECT_INIT (project_init.py)
    "add_tasks",                # SPRINT_PLANNING (project_init.py)
    "validate_plan",            # SPRINT_PLANNING (planning.py)
    "gather_context",           # DESIGN.GATHER (gathering.py)
    "save_to_ckg",              # DESIGN.KNOWLEDGE_UPDATE (kg.py)
    "refine_diagnose",          # REFINE.DIAGNOSE (refinement.py)
    "kg_search",                # memória pública (kg.py)
    "kg_multi",                 # memória federada (kg.py)
}


class TestRegistryCompleteness:
    """bootstrap() debe producir el registry poblado (composition root D1)."""

    def test_bootstrap_tasks_contains_workflow_actions(self):
        from features.engine.bootstrap import bootstrap

        registry, _se = bootstrap()
        available = set(registry.list_tasks())
        missing = EXPECTED_WORKFLOW_TASKS - available
        assert not missing, f"bootstrap_tasks() no registró: {sorted(missing)}"

    def test_bootstrap_se_engine_has_side_effects(self):
        from features.engine.bootstrap import bootstrap

        _registry, se_engine = bootstrap()
        # Los side effects reales del flujo deben estar registrados
        # (transform_spec, create_adr_from_spec) — no solo los mocks.
        handlers = getattr(se_engine, "_handlers", {})
        assert "transform_spec" in handlers, "transform_spec no registrado"
        assert "create_adr_from_spec" in handlers, "create_adr_from_spec no registrado"

    def test_di_exposes_task_registry_service(self):
        """capa_rout.md D1: get_module_service('task_registry') devuelve el
        registry poblado — sin esto, _build_workflow_services no puede
        inyectar tasks en producción."""
        from features.engine.bootstrap import (
            _module_registry_singleton,
            get_module_service,
        )

        reg = _module_registry_singleton()
        assert reg.has_service("task_registry"), "task_registry no es servicio DI"
        assert reg.has_service("side_effect_engine"), "side_effect_engine no es servicio DI"

        task_registry = get_module_service("task_registry")
        missing = EXPECTED_WORKFLOW_TASKS - set(task_registry.list_tasks())
        assert not missing, f"DI task_registry incompleto: {sorted(missing)}"
