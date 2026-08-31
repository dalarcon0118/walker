"""tests/test_rag_pipeline_bdd.py — RAG Pipeline E2E: valida el pipeline completo
CPG → CKG → Delta → Trace usando las tasks públicas directamente.

Nivel 3 de testing (según SPEC-003):
- test_rag_cpg_to_ckg_pipeline
- test_rag_delta_to_ckg_spec_flow
- test_rag_trace_links_workflow_to_delta
- test_rag_kg_extract_indexes_code
"""
from __future__ import annotations

import os
from pathlib import Path

from shared.core.engine.domain.execution import ExecutionContext, TaskResult
from features.delta_tracking import spring_service
from tasks.actions.inspect_codebase import inspect_codebase
from tasks.actions.knowledge_query import knowledge_query
from tasks.actions.trace_push import trace_push
from tasks.actions.extract_knowledge import extract_knowledge
from tests.conftest import make_chain_dir, make_walker_project
from app.memories.registration import apply_memory_indexes


# ── Helpers de setup ────────────────────────────────────────────────────

def _setup_project(tmp_path: Path) -> tuple[str, str]:
    """Crea el proyecto walker + chain_dir sprint-rag (builders compartidos en conftest)."""
    return make_walker_project(tmp_path), make_chain_dir(tmp_path, "sprint-rag")


def _setup_delta(chain_dir: str) -> str:
    svc = spring_service(chain_dir=chain_dir)
    assert svc is not None
    spring_id = svc.get_spring_id(chain_dir)
    svc.create_spring(spring_id, goal="RAG pipeline test", project_id="walker", session_id="sess-rag")
    svc.add_spec(
        spring_id,
        objective="RAG pipeline spec",
        domain="test",
        spec_type="feature",
        spec_index=0,
        status="draft",
    )
    return spring_id


def _build_exec_ctx(chain_dir: str) -> ExecutionContext:
    return ExecutionContext.create(
        session_id="sess-rag",
        chain_dir=chain_dir,
        task_id="SPEC-RAG",
        baggage={},
    )


# ── Tests RAG Pipeline ──────────────────────────────────────────────────

class TestRagPipeline:
    """Pipeline RAG: CPG → CKG → Delta → Trace."""

    def test_rag_cpg_to_ckg_pipeline(self, tmp_path):
        project_root, chain_dir = _setup_project(tmp_path)
        _setup_delta(chain_dir)
        ctx = _build_exec_ctx(chain_dir)

        result: TaskResult = inspect_codebase(ctx, {"scope": project_root})
        assert result.ok, f"inspect_codebase failed: {result.error}"

    def test_rag_delta_to_ckg_spec_flow(self, tmp_path):
        project_root, chain_dir = _setup_project(tmp_path)
        spring_id = _setup_delta(chain_dir)
        ctx = _build_exec_ctx(chain_dir)

        svc = spring_service(chain_dir=chain_dir)
        specs = svc.get_specs(spring_id)
        assert len(specs) >= 1

        result: TaskResult = knowledge_query(ctx, {"action": "search", "query": "RAG pipeline spec"})
        assert result.ok, f"knowledge_query failed: {result.error}"

    def test_rag_trace_links_workflow_to_delta(self, tmp_path):
        project_root, chain_dir = _setup_project(tmp_path)
        ctx = _build_exec_ctx(chain_dir)

        # Sin mocks: trace_push resuelve el CognitiveGraph real sobre el project
        # root derivado de chain_dir (get_module_service -> _build_graph).
        result: TaskResult = trace_push(ctx, {
            "event_type": "rag_test",
            "data": {"test": True},
            "task_id": "SPEC-RAG",
            "node": "SPRINT_PLANNING",
            "sub_state": "PLANNING",
        })
        assert result.ok, f"trace_push failed: {result.error}"
        event_id = result.data.get("event_id")
        assert event_id, "trace_push no generó event_id"

        # Verificación REAL de persistencia: el evento debe estar en el índice
        # trace del mismo root que trace_push usó (audit 2026-08-11: el mock
        # de get_module_service hacía este test ciego a que cg.store no corra).
        from features.project import ProjectService
        from shared.libs.cognitive_graph import CognitiveGraph

        paths = ProjectService.resolve_paths(chain_dir)
        cg = CognitiveGraph(paths.project_root, register_defaults=apply_memory_indexes)
        trace_keys = set(cg.keys("trace"))
        assert event_id in trace_keys, (
            f"evento {event_id} no persistido en el TraceGraph: {sorted(trace_keys)}"
        )
        stored = cg.get("trace", event_id)
        assert stored is not None, "el evento trace no se re-lee del índice"
        source = stored["_source"]
        assert source.get("event_type") == "rag_test", source
        assert source.get("task_id") == "SPEC-RAG", source
        assert source.get("node") == "SPRINT_PLANNING", source
        assert source.get("sub_state") == "PLANNING", source

    def test_rag_kg_extract_indexes_code(self, tmp_path):
        project_root, chain_dir = _setup_project(tmp_path)
        _setup_delta(chain_dir)
        ctx = _build_exec_ctx(chain_dir)

        code_file = os.path.join(project_root, "sample.py")
        with open(code_file, "w") as f:
            f.write("def hello():\n    return 'world'\n")

        result: TaskResult = extract_knowledge(ctx, {"scope": "functions", "files": [code_file]})
        assert result.ok, f"extract_knowledge failed: {result.error}"
