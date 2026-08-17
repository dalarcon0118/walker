"""tests/test_registry_completeness_bdd.py — Completitud del TaskRegistry (AGENTS.md).

Patrón obligatorio del AGENTS.md: todo registry construido por bootstrap() o
build_*() debe tener un test de completitud que valide el conjunto esperado
de componentes. Aquí validamos que bootstrap_tasks()/bootstrap() registran
las tasks reales del workflow — la rotura de composición (registry vacío)
debe ser visible en CI, no enmascarada por register_mock_tasks().
"""
from __future__ import annotations


EXPECTED_WORKFLOW_TASKS = {
    "plan_sprint",          # SPRINT.PLANNING (sprint_planning.py → SprintApplicationService.plan_sprint)
    "spec_task",            # SPRINT.TASK_SPEC (sprint_planning.py → specify_spec)
    "review_code",          # IMPLEMENT.CODE_REVIEW_SCAN (code_review.py → CPG port)
    "close_sprint",         # SPRINT.CLOSE (sprint_planning.py → close_sprint)
    "implement_task",       # IMPLEMENT.WORK (implement.py)
    "run_tests",            # VERIFY.QA_RUN (implement.py)
    "verify_predicates",    # VERIFY.PREDICATE_CHECK (verify.py)
    "build_code_index",     # PROJECT_INIT (initialize_project.py → inspect_codebase)
    "initialize_knowledge_base",  # PROJECT_INIT (initialize_project.py)
    "create_first_sprint",  # PROJECT_INIT (initialize_project.py → plan_sprint)
    "validate_plan",        # SPRINT_PLANNING (sprint_planning.py)
    "gather_context",       # DESIGN.GATHER (context_gathering.py)
    "documenting_knowledge",    # DESIGN.KNOWLEDGE_UPDATE (knowledge_adapter.py)
    "refine_diagnose",          # REFINE.DIAGNOSE (refine.py)
    "recall_knowledge",         # memoria publica (knowledge_adapter.py)
    "search_memories",          # memoria federada (knowledge_adapter.py)
    "extract_knowledge",        # PROJECT_INIT / knowledge extraction (extract_knowledge.py)
    "agent_context",            # consulta consolidada 4-capas (knowledge_agent_context.py)
    "knowledge_query",          # consulta unificada CKG (knowledge_query.py)
    "inspect_codebase",         # CPG analysis (inspect_codebase.py)
    "task_context",             # IMPLEMENT context (intelligence.py)
    "diagnose_failure",         # REFINE diagnosis (intelligence.py)
    "verify_evidence",          # VERIFY evidence (intelligence.py)
    "sprint_evidence",          # SPRINT.PLANNING evidence (intelligence.py)
}


class TestRegistryCompleteness:
    """bootstrap() debe producir el registry poblado (composition root D1)."""

    def test_bootstrap_tasks_contains_workflow_actions(self):
        from features.main.container import AppContainer

        app = AppContainer.build()
        registry = app.task_registry
        available = set(registry.list_tasks())
        missing = EXPECTED_WORKFLOW_TASKS - available
        assert not missing, f"AppContainer.build() no registró: {sorted(missing)}"

    def test_bootstrap_se_engine_has_side_effects(self):
        from features.main.container import AppContainer

        app = AppContainer.build()
        se_engine = app.side_effect_engine
        handlers = getattr(se_engine, "_handlers", {})
        assert "transform_spec" in handlers, "transform_spec no registrado"
        assert "create_adr" in handlers, "create_adr no registrado"

    def test_di_exposes_task_registry_service(self):
        """capa_rout.md D1: get_module_service('task_registry') devuelve el
        registry poblado — sin esto, _build_workflow_services no puede
        inyectar tasks en producción."""
        from features.main.container import AppContainer

        app = AppContainer.build()
        from core.engine.bootstrap import get_module_service

        task_registry = get_module_service("task_registry")
        missing = EXPECTED_WORKFLOW_TASKS - set(task_registry.list_tasks())
        assert not missing, f"DI task_registry incompleto: {sorted(missing)}"
