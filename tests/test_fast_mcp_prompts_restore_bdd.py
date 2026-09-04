"""tests/test_fast_mcp_prompts_restore_bdd.py — Prompt subsystem restoration.

Valida que el subsistema de prompts restaurado (commit 63efa79 lo eliminó)
funciona de nuevo en fast_mcp:
  1. `PromptManager.resolve` carga templates canónicos y renderiza datos.
  2. `resolve_for_sub_state` carga dinámicamente el prompt por nodo cuando
     el sub-estado es `execution == llm_task` (el mecanismo reportado por
     el usuario).
  3. `OrchestrationService.status()` expone el prompt del sub-estado actual
     cuando hay un prompt_manager en el bundle.
  4. `available_templates()` lista los templates de nodos restaurados.
"""
import os
from pathlib import Path

import pytest
from dataclasses import dataclass, field
from typing import Any

from fast_mcp.features.workflow.application.bundle import EngineBundle
from fast_mcp.features.workflow.application.orchestrator import OrchestrationService
from fast_mcp.features.workflow.application.prompts import IPromptManager, PromptManager
from fast_mcp.features.workflow.application.prompts.renderer import TemplateRenderer
from fast_mcp.features.workflow.application.prompts.static_loader import (
    canonical_nodes_dir,
    load_static,
)
from fast_mcp.features.workflow.domain.entities import (
    Gate,
    SubState,
    WorkflowGraph,
    WorkflowNode,
    WorkflowState,
)
from fast_mcp.features.workflow.domain.enums import ExecutionType
from fast_mcp.features.workflow.domain.errors import TemplateNotFoundError
from fast_mcp.features.workflow.domain.execution import ExecutionContext
from fast_mcp.features.workflow.domain.ports import (
    ITracer,
    IWorkflowIO,
    IWorkflowStore,
    ISideEffectEngine,
)


# ── Mocks (same as workflow application tests) ───────────────────────────

@dataclass
class MockWorkflowStore(IWorkflowStore):
    _graph: WorkflowGraph | None = None
    _state: WorkflowState | None = None

    def load_graph(self) -> Any:
        return self._graph

    def load_state(self, chain_dir: str) -> Any:
        return self._state if self._state is not None else WorkflowState()

    def save_state(self, chain_dir: str, state: Any) -> Any:
        self._state = state
        return state


@dataclass
class MockTracer(ITracer):
    events: list = field(default_factory=list)

    def trace(self, chain_dir, event_type, data=None, task_id=None, session_id=None):
        self.events.append(("trace", event_type))

    def trace_event(self, event):
        self.events.append(("event", event.event_type))

    def trace_submit(self, chain_dir, sub_state, decision, evidence, task_id=None):
        self.events.append(("submit", sub_state))

    def trace_transition(self, chain_dir, gate, from_node, to_node, task_id=None):
        self.events.append(("transition", gate, from_node, to_node))

    def trace_error(self, chain_dir, error_type, message, task_id=None):
        self.events.append(("error", error_type))

    def has_kg_query(self, chain_dir, task_id="", session_id="", layers=None):
        return True


@dataclass
class MockWorkflowIO(IWorkflowIO):
    def run_script(self, script, chain_dir=None):
        return {"ok": True, "stdout": "", "stderr": "", "exit_code": 0}

    def eprint(self, *args, **kwargs):
        pass


@dataclass
class MockSideEffects(ISideEffectEngine):
    def run_compensation_handler(self, handler_name, ctx):
        return {"ok": True, "skipped": True}


def _ctx(task_id: str = "T1", session_id: str = "S1") -> ExecutionContext:
    return ExecutionContext(
        session_id=session_id,
        trace_id="trace-1",
        span_id="span-1",
        chain_dir="/tmp/test-project",
        task_id=task_id,
    )


# ── Renderer (pure) ──────────────────────────────────────────────────────

class TestTemplateRenderer:

    def test_render_replaces_placeholders(self):
        r = TemplateRenderer()
        out = r.render("Hola {nombre}, nodo {node_id}", {"nombre": "Walker", "node_id": "X"})
        assert out == "Hola Walker, nodo X"

    def test_render_missing_returns_empty(self):
        r = TemplateRenderer()
        assert r.render("a {no_existe} b", {}) == "a  b"

    def test_render_tojson(self):
        r = TemplateRenderer()
        out = r.render("{{datos|tojson}}", {"datos": {"a": 1}})
        import json
        assert json.loads(out) == {"a": 1}

    def test_render_dict(self):
        r = TemplateRenderer()
        out = r.render_dict({"title": "Node {n}", "body": ["{x}", "static"]}, {"n": "1", "x": "v"})
        assert out == {"title": "Node 1", "body": ["v", "static"]}


# ── Static loader / canonical SSOT ───────────────────────────────────────

class TestCanonicalLoader:

    def test_canonical_nodes_dir_has_restored_templates(self):
        import os
        nodes = canonical_nodes_dir()
        assert os.path.isdir(nodes)
        expected = {"analyze", "implement", "refine", "verify", "sprint", "start"}
        have = {d for d in os.listdir(nodes) if os.path.isdir(os.path.join(nodes, d))}
        assert expected.issubset(have)

    def test_load_static_protocol_mcp(self):
        text = load_static("protocol/mcp")
        assert "MCP" in text or "jsonrpc" in text.lower() or len(text) > 20


# ── PromptManager (restored manager) ─────────────────────────────────────

class TestPromptManager:

    def test_available_templates_restored(self):
        pm = PromptManager(chain_dir="/tmp/test-project")
        available = pm.available_templates()
        # Restored node templates (48 files) are discoverable.
        assert "analyze/analyze" in available
        assert "implement/work" in available
        assert "refine/diagnose" in available
        assert "verify/qa" in available
        assert len(available) >= 40

    def test_resolve_canonical_template_and_render(self):
        pm = PromptManager(chain_dir="/tmp/test-project")
        out = pm.resolve("analyze/analyze", context={"node_id": "ANZ"})
        assert isinstance(out, str)
        # Renderiza el template canónico del nodo; no levanta aunque no haya
        # placeholders que sustituir.
        assert len(out) > 50
        assert "ANALYZE" in out.upper()
        assert "Technical Analyst" in out

    def test_resolve_for_sub_state_llm_task(self):
        pm = PromptManager(chain_dir="/tmp/test-project")
        result = pm.resolve_for_sub_state(
            node_id="analyze",
            sub_state_key="analyze",
            execution="llm_task",
            fields={"prompt_id": "", "prompt": ""},
            extra_data={"task_id": "T-1"},
        )
        assert result["type"] == "llm_task"
        assert result["template"]
        assert result["node_id"] == "analyze"
        assert "T-1" in result["template"] or "task_id" in result["data"]

    def test_resolve_for_sub_state_non_llm_returns_marker(self):
        pm = PromptManager(chain_dir="/tmp/test-project")
        result = pm.resolve_for_sub_state(
            node_id="implement",
            sub_state_key="work",
            execution="auto",
            fields={"prompt": ""},
        )
        assert result["type"] == "auto"

    def test_resolve_for_sub_state_prefers_prompt_id(self):
        pm = PromptManager(chain_dir="/tmp/test-project")
        result = pm.resolve_for_sub_state(
            node_id="analyze",
            sub_state_key="gather",
            execution="llm_task",
            fields={"prompt_id": "analyze/analyze", "prompt": ""},
        )
        assert result["type"] == "llm_task"
        assert result["template"]

    def test_missing_template_raises_template_not_found(self):
        pm = PromptManager(chain_dir="/tmp/test-project")
        with pytest.raises(TemplateNotFoundError):
            pm.resolve("no/such/template")


# ── OrchestrationService exposes current prompt ──────────────────────────

def _make_graph_with_llm_analyze() -> WorkflowGraph:
    return WorkflowGraph(
        nodes={
            "START": WorkflowNode(
                node_id="START",
                sub_states=[SubState(key="INIT", label="Initialize")],
                label="Start",
            ),
            "analyze": WorkflowNode(
                node_id="analyze",
                sub_states=[
                    SubState(
                        key="analyze",
                        label="Analyze",
                        execution=ExecutionType.LLM_TASK,
                    )
                ],
                label="Analyze",
            ),
        },
        edges=[
            Gate(name="start_analyze", source_node="START", target_node="analyze"),
        ],
        edges_by_source={
            "START": [Gate(name="start_analyze", source_node="START", target_node="analyze")],
        },
        initial_state="START",
    )


def _bundle_with_prompt_manager(graph: WorkflowGraph, state: WorkflowState) -> EngineBundle:
    store = MockWorkflowStore(_graph=graph, _state=state)
    return EngineBundle(
        workflow_store=store,
        workflow_io=MockWorkflowIO(),
        tracer=MockTracer(),
        side_effects=MockSideEffects(),
        chain_dir="/tmp/test-project",
        prompt_manager=PromptManager(chain_dir="/tmp/test-project"),
    )


class TestStatusSurfacesPrompt:

    def test_status_includes_prompt_for_llm_task(self):
        graph = _make_graph_with_llm_analyze()
        state = WorkflowState(current_node="analyze", current_sub_state="analyze", sub_state_index=0)
        bundle = _bundle_with_prompt_manager(graph, state)
        orch = OrchestrationService(bundle=bundle)

        result = orch.status(_ctx())
        assert result["ok"] is True
        prompt = result.get("prompt")
        assert prompt is not None
        assert prompt["type"] == "llm_task"
        assert prompt["node"] == "analyze"
        assert prompt["template"]  # loaded from templates/nodes/analyze/analyze.prompt.json

    def test_status_without_prompt_manager_omits_prompt(self):
        graph = _make_graph_with_llm_analyze()
        state = WorkflowState(current_node="analyze", current_sub_state="analyze", sub_state_index=0)
        store = MockWorkflowStore(_graph=graph, _state=state)
        bundle = EngineBundle(
            workflow_store=store,
            workflow_io=MockWorkflowIO(),
            tracer=MockTracer(),
            side_effects=MockSideEffects(),
            chain_dir="/tmp/test-project",
        )
        orch = OrchestrationService(bundle=bundle)
        result = orch.status(_ctx())
        assert result["ok"] is True
        assert result.get("prompt") is None


# ── Evidence (evidence.py) ────────────────────────────────────────────────

class TestEvidence:

    def test_format_detail_block_shell(self):
        from fast_mcp.features.workflow.application.prompts.evidence import format_detail_block

        detail = {"method": "shell", "command": "python -m pytest", "exit_code": 0, "stdout": "1 passed", "stderr": ""}
        block = format_detail_block(detail)
        assert "Shell" in block
        assert "python -m pytest" in block
        assert "Exit code: 0" in block

    def test_format_detail_block_symbol(self):
        from fast_mcp.features.workflow.application.prompts.evidence import format_detail_block

        detail = {"method": "symbol_exists", "symbol": "MyClass", "file": "src/main.py"}
        block = format_detail_block(detail)
        assert "Symbol" in block
        assert "MyClass" in block

    def test_build_evidence_blocks_passed_and_failed(self):
        from fast_mcp.features.workflow.application.prompts.evidence import build_evidence_blocks

        checks = {
            "total": 2,
            "passed": 1,
            "passed_items": [
                {"predicate": "imports ok", "detail": {"method": "shell", "command": "python3 -c 'import x'", "exit_code": 0, "stdout": "", "stderr": ""}},
            ],
            "failed": [
                {"predicate": "tests pass", "detail": {"method": "shell", "command": "pytest", "exit_code": 1, "stdout": "1 failed", "stderr": "error"}},
            ],
        }
        blocks = build_evidence_blocks(checks)
        assert "CUMPLEN" in blocks
        assert "NO CUMPLEN" in blocks

    def test_build_verdict_can_confirm(self):
        from fast_mcp.features.workflow.application.prompts.evidence import build_verdict

        verdict = build_verdict(5, 5, [], True)
        assert "COMPLETA" in verdict
        assert "5/5" in verdict

    def test_build_evidence_report(self):
        from fast_mcp.features.workflow.application.prompts.evidence import build_evidence_report

        checks = {"total": 1, "passed": 1, "passed_items": [{"predicate": "ok", "detail": {"method": "shell", "command": "echo", "exit_code": 0, "stdout": "", "stderr": ""}}], "failed": []}
        report = build_evidence_report("T-1", checks)
        assert "T-1" in report
        assert "META-INSTRUCTION" in report

    def test_risk_label(self):
        from fast_mcp.features.workflow.application.prompts.evidence import risk_label

        assert risk_label(0.1, [(0.2, "baja"), (1.0, "alta")]) == "baja"
        assert risk_label(0.5, [(0.2, "baja"), (1.0, "alta")]) == "alta"


# ── Gate Briefing (gate_briefing.py) ─────────────────────────────────────

class TestGateBriefing:

    def test_render_gate_briefing_returns_string(self):
        from fast_mcp.features.workflow.application.prompts.gate_briefing import render_gate_briefing

        out = render_gate_briefing(gate_name="APPROVE", task_id="T-1", what_happened="Done", evidence="OK")
        assert isinstance(out, str)
        assert "APPROVE" in out

    def test_what_happened_qa_analyze(self):
        from fast_mcp.features.workflow.application.prompts.gate_briefing import what_happened_from_state

        fields = {"q1": "What exists?", "a1": "Components A, B", "q2": "Impact?", "a2": "Affects A", "q3": "Approach?", "a3": "Strategy"}
        out = what_happened_from_state(fields)
        assert "análisis" in out.lower() or "Q1" in out
        assert "Components A, B" in out

    def test_what_happened_decision(self):
        from fast_mcp.features.workflow.application.prompts.gate_briefing import what_happened_from_state

        fields = {"decision": "approve", "feedback": "Looks good"}
        out = what_happened_from_state(fields)
        assert "approve" in out.lower()
        assert "Looks good" in out

    def test_what_happened_empty(self):
        from fast_mcp.features.workflow.application.prompts.gate_briefing import what_happened_from_state

        out = what_happened_from_state(None)
        assert "sin datos" in out.lower() or "no hay" in out.lower()


# ── Collector (collector.py) ──────────────────────────────────────────────

class TestCollector:

    def test_collect_activity_without_ckg(self):
        from fast_mcp.features.workflow.application.prompts.collector import collect_activity

        # No ckg_searcher → degrades gracefully
        activity = collect_activity("/tmp/test-project", "MY_NODE", ckg_searcher=None)
        assert activity["id"] == "MY_NODE"
        assert activity["description"] == ""

    def test_collect_project_info_without_ckg(self):
        from fast_mcp.features.workflow.application.prompts.collector import collect_project_info

        info = collect_project_info("/tmp/test-project", ckg_searcher=None)
        assert info["architecture"] == ""
        assert info["patterns"] == []

    def test_collect_knowledge_without_ckg(self):
        from fast_mcp.features.workflow.application.prompts.collector import collect_knowledge

        knowledge = collect_knowledge("/tmp/test-project", ckg_searcher=None)
        assert knowledge["global"] == []
        assert knowledge["relevant"] == []

    def test_collect_available_tasks_no_registry(self):
        from fast_mcp.features.workflow.application.prompts.collector import collect_available_tasks

        tasks = collect_available_tasks("/tmp/test-project", task_registry=None)
        assert tasks == []

    def test_collect_available_tasks_with_registry(self):
        from fast_mcp.features.workflow.application.prompts.collector import collect_available_tasks

        @dataclass
        class FakeRegistry:
            def list_tasks_with_meta(self):
                return [{"name": "run_tests", "description": "Run tests", "usage": "walker test"}]

        tasks = collect_available_tasks("/tmp/test-project", task_registry=FakeRegistry())
        assert len(tasks) == 1
        assert tasks[0]["name"] == "run_tests"

    def test_collect_state_no_store(self):
        from fast_mcp.features.workflow.application.prompts.collector import collect_state

        state = collect_state("/tmp/test-project", workflow_store=None, sprint_state_loader=None)
        assert state["node"] == ""
        assert state["sub_state"] == ""

    def test_collect_state_with_store(self):
        from fast_mcp.features.workflow.application.prompts.collector import collect_state

        store = MockWorkflowStore(_state=WorkflowState(current_node="WORK", current_sub_state="EXEC"))
        state = collect_state("/tmp/test-project", workflow_store=store, sprint_state_loader=None)
        assert state["node"] == "WORK"
        assert state["sub_state"] == "EXEC"

    def test_collect_all_degrades_gracefully(self):
        from fast_mcp.features.workflow.application.prompts.collector import collect_all

        data = collect_all(
            "/tmp/test-project",
            task_registry=None,
            workflow_store=None,
            ckg_searcher=None,
            sprint_state_loader=None,
        )
        assert "activity" in data
        assert "project" in data
        assert "knowledge" in data
        assert "state" in data
        assert data["chain_dir"] == "/tmp/test-project"

    def test_collect_activity_with_mock_ckg(self):
        from fast_mcp.features.workflow.application.prompts.collector import collect_activity

        @dataclass
        class FakeSearcher:
            def search(self, index, query):
                return {"hits": {"total": 1, "hits": [{"_source": {"statement": "Architecture is clean"}}]}}

        activity = collect_activity("/tmp/test-project", "ARCHITECTURE", ckg_searcher=FakeSearcher())
        assert activity["description"] == "Architecture is clean"

    def test_collect_all_wires_everything(self):
        from fast_mcp.features.workflow.application.prompts.collector import collect_all

        store = MockWorkflowStore(_state=WorkflowState(current_node="ANALYZE", current_sub_state="GATHER"))

        @dataclass
        class FakeRegistry:
            def list_tasks_with_meta(self):
                return [{"name": "test", "description": "t", "usage": "u"}]

        @dataclass
        class FakeCkg:
            def search(self, index, query):
                return {"hits": {"total": 0, "hits": []}}

        def fake_sprint_loader(chain_dir):
            return ("SPRINT-1", "active", "SPEC-1")

        data = collect_all(
            "/tmp/test-project",
            task_registry=FakeRegistry(),
            workflow_store=store,
            ckg_searcher=FakeCkg(),
            sprint_state_loader=fake_sprint_loader,
        )
        assert data["node"] == "ANALYZE"
        assert data["sprint_id"] == "SPRINT-1"
        assert data["status"] == "active"
        assert len(data["tasks"]) == 1


# ── Protocol Builder (protocol_builder.py) ────────────────────────────────

class TestProtocolBuilder:

    def test_build_mcp_protocol_returns_static_template(self):
        from fast_mcp.features.workflow.application.prompts.protocol_builder import build_mcp_protocol

        protocol = build_mcp_protocol()
        assert isinstance(protocol, str)
        assert len(protocol) > 100
        # Should contain MCP-related content
        assert "MCP" in protocol or "protocol" in protocol.lower()


# ── Workflow config restoration (data/workflow/) ──────────────────────────
# Valida que la configuración del workflow (SSOT del grafo BPMN) restaurada
# desde data/workflow/ carga con el GraphRepository canónico de fast_mcp
# y que cada sub_estado llm_task tiene su template de prompt restaurado.

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_JSON = REPO_ROOT / "data" / "workflow" / "workflow.json"

EXPECTED_WORKFLOW_NODES = {
    "START",
    "SPRINT",
    "PROJECT_INIT",
    "DESIGN",
    "ANALYZE",
    "IMPLEMENT",
    "VERIFY",
    "REFINE",
}


class TestWorkflowConfigRestored:

    def test_workflow_json_exists(self):
        assert WORKFLOW_JSON.exists(), (
            f"workflow.json no restaurado: {WORKFLOW_JSON}"
        )

    def test_restored_workflow_loads_with_graph_repository(self):
        from fast_mcp.features.workflow.infrastructure.graph_repository import GraphRepository

        graph = GraphRepository(str(WORKFLOW_JSON)).load()

        assert graph.initial_state == "START"
        assert graph.nodes is not None
        assert empirical_subset(EXPECTED_WORKFLOW_NODES, set(graph.nodes))
        # Edges (gates) definidos
        assert len(graph.edges) > 0
        # context_bag propagado (SSOT de placeholders)
        assert graph.on_events is not None

    def test_every_llm_task_sub_state_has_restored_prompt(self):
        """Cada sub_estado execution == llm_task apunta a un template restaurado."""
        from fast_mcp.features.workflow.infrastructure.graph_repository import GraphRepository
        from fast_mcp.features.workflow.application.prompts.static_loader import canonical_nodes_dir

        graph = GraphRepository(str(WORKFLOW_JSON)).load()
        templates_dir = Path(canonical_nodes_dir())
        missing = []
        for node_id, node in graph.nodes.items():
            for ss in node.sub_states:
                if getattr(ss, "execution", "") == "llm_task" or is_llm_task(ss):
                    pid = ss.prompt_id or ss.prompt
                    if not pid:
                        continue
                    # template canónico: nodes/<nodo>/<pid-sin-prefijo>.prompt.json
                    tpl = templates_dir / (pid + ".prompt.json")
                    if not tpl.exists():
                        missing.append(f"{node_id}/{pid}")
        assert not missing, f"Templates faltantes para llm_task: {missing}"

    def test_every_prompt_id_in_nodes_has_template(self):
        """Todos los prompt_id referenciados por los nodos tienen template."""
        from fast_mcp.features.workflow.infrastructure.graph_repository import GraphRepository
        from fast_mcp.features.workflow.application.prompts.static_loader import canonical_nodes_dir

        # Leer prompt_ids directo del JSON (resolución modular ya embebida)
        import json as _json
        with open(WORKFLOW_JSON) as f:
            base = _json.load(f)
        nodes_dir = WORKFLOW_JSON.parent / "nodes"
        templates_dir = Path(canonical_nodes_dir())
        missing = []
        for node_file in sorted(nodes_dir.glob("*.node.json")):
            with open(node_file) as f:
                node_data = _json.load(f)
            for ss in node_data.get("sub_states", []):
                pid = ss.get("prompt_id") or ss.get("prompt")
                if not pid:
                    continue
                tpl = templates_dir / (pid + ".prompt.json")
                if not tpl.exists():
                    missing.append(f"{node_file.name}/{pid}")
        assert not missing, f"Templates faltantes: {missing}"


def is_llm_task(ss) -> bool:
    """Un sub_estado es llm_task si pide prompt o su execution es llm_task."""
    return (getattr(ss, "execution", "") == "llm_task"
            or bool(getattr(ss, "prompt_id", "") or getattr(ss, "prompt", "")))


def empirical_subset(expected: set, actual: set) -> bool:
    """expected ⊆ actual (los nodos clave del workflow deben existir)."""
    return expected.issubset(actual)
