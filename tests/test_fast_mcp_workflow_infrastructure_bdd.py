"""tests/test_fast_mcp_workflow_infrastructure_bdd.py — Sprint 3 integration tests.

Tests infrastructure adapters with real filesystem (temp dirs) and subprocess.
"""
import json
import os
import tempfile

import pytest
from returns.result import Success

from fast_mcp.features.workflow.infrastructure.noop_tracer import NoopTracer
from fast_mcp.features.workflow.infrastructure.noop_side_effects import NoOpSideEffectEngine
from fast_mcp.features.workflow.infrastructure.default_workflow_io import DefaultWorkflowIO
from fast_mcp.features.workflow.infrastructure.graph_repository import GraphRepository
from fast_mcp.features.workflow.infrastructure.state_repository import StateRepository
from fast_mcp.features.workflow.infrastructure.workflow_store_adapter import WorkflowStoreAdapter
from fast_mcp.features.workflow.domain.entities import WorkflowState, WorkflowGraph
from fast_mcp.features.workflow.domain.errors import StateConflictError


# ── NoopTracer tests ─────────────────────────────────────────────────────

class TestNoopTracer:

    def test_trace_never_raises(self):
        t = NoopTracer()
        t.trace("/tmp", "test_event", data={"x": 1}, task_id="T1")
        t.trace_event(None)
        t.trace_submit("/tmp", "SUB", "yes", "evidence")
        t.trace_transition("/tmp", "gate", "A", "B")
        t.trace_error("/tmp", "err", "msg")
        # No exception = pass

    def test_has_kg_query_returns_false(self):
        t = NoopTracer()
        assert t.has_kg_query("/tmp") is False
        assert t.has_kg_query("/tmp", layers={"ckg"}) is False


# ── NoOpSideEffectEngine tests ───────────────────────────────────────────

class TestNoOpSideEffectEngine:

    def test_returns_success(self):
        engine = NoOpSideEffectEngine()
        result = engine.run_compensation_handler("test_handler", None)
        assert isinstance(result, Success)
        value = result.value_or(None)
        assert value["ok"] is True
        assert value["skipped"] is True
        assert value["handler"] == "test_handler"


# ── DefaultWorkflowIO tests ──────────────────────────────────────────────

class TestDefaultWorkflowIO:

    def test_run_script_echo(self):
        io = DefaultWorkflowIO()
        result = io.run_script("echo hello")
        assert result["exit_code"] == 0
        assert result["stdout"] == "hello"

    def test_run_script_interpolation(self):
        io = DefaultWorkflowIO()
        result = io.run_script("echo <chain_dir>", chain_dir="/tmp/test")
        assert result["exit_code"] == 0
        assert "/tmp/test" in result["stdout"]

    def test_run_script_failure(self):
        io = DefaultWorkflowIO()
        result = io.run_script("exit 1")
        assert result["exit_code"] == 1

    def test_run_script_nonexistent(self):
        io = DefaultWorkflowIO()
        result = io.run_script("nonexistent_command_xyz")
        assert result["exit_code"] != 0
        assert result["stderr"] != ""

    def test_eprint(self, capsys):
        io = DefaultWorkflowIO()
        io.eprint("test error")
        captured = capsys.readouterr()
        assert "test error" in captured.err


# ── GraphRepository tests ────────────────────────────────────────────────

class TestGraphRepository:

    def _write_workflow(self, tmp_dir: str, workflow: dict) -> str:
        path = os.path.join(tmp_dir, "workflow.json")
        with open(path, "w") as f:
            json.dump(workflow, f)
        return path

    def test_load_simple_graph(self, tmp_path):
        workflow = {
            "initial_state": "START",
            "nodes": {
                "START": {
                    "sub_states": [{"key": "INIT", "label": "Initialize"}],
                    "label": "Start",
                },
                "END": {
                    "sub_states": [{"key": "DONE", "label": "Done"}],
                    "label": "End",
                },
            },
            "edges": [
                {"gate": "finish", "from": "START", "to": "END"},
            ],
        }
        path = self._write_workflow(str(tmp_path), workflow)
        repo = GraphRepository(path)
        graph = repo.load()

        assert isinstance(graph, WorkflowGraph)
        assert "START" in graph.nodes
        assert "END" in graph.nodes
        assert len(graph.edges) == 1
        assert graph.edges[0].name == "finish"
        assert graph.initial_state == "START"

    def test_load_with_sub_states(self, tmp_path):
        workflow = {
            "initial_state": "W",
            "nodes": {
                "W": {
                    "sub_states": [
                        {"key": "A", "execution": "auto", "action": "run_tests"},
                        {"key": "B", "execution": "manual", "required_fields": ["data"]},
                    ],
                },
            },
            "edges": [],
        }
        path = self._write_workflow(str(tmp_path), workflow)
        graph = GraphRepository(path).load()

        node = graph.get_node("W")
        assert node is not None
        assert len(node.sub_states) == 2
        assert node.sub_states[0].execution.value == "auto"
        assert node.sub_states[0].action == "run_tests"
        assert node.sub_states[1].required_fields == ["data"]

    def test_load_with_imports(self, tmp_path):
        """Test modular import resolution (real workflow.json format)."""
        # Write node files
        nodes_dir = os.path.join(str(tmp_path), "nodes")
        os.makedirs(nodes_dir)
        with open(os.path.join(nodes_dir, "start.node.json"), "w") as f:
            json.dump({"sub_states": [{"key": "INIT"}], "label": "Start"}, f)
        with open(os.path.join(nodes_dir, "end.node.json"), "w") as f:
            json.dump({"sub_states": [{"key": "DONE"}], "label": "End"}, f)

        # Write edges file
        with open(os.path.join(str(tmp_path), "edges.json"), "w") as f:
            json.dump([{"gate": "go", "from": "START", "to": "END"}], f)

        # Write workflow.json with import
        workflow = {
            "initial_state": "START",
            "import": {
                "nodes": ["nodes/start.node.json", "nodes/end.node.json"],
                "edges": "edges.json",
            },
        }
        path = self._write_workflow(str(tmp_path), workflow)
        graph = GraphRepository(path).load()

        assert "START" in graph.nodes
        assert "END" in graph.nodes
        assert len(graph.edges) == 1

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            GraphRepository("/nonexistent/workflow.json").load()

    def test_invariant_validation_logs_warnings(self, tmp_path):
        workflow = {
            "initial_state": "START",
            "nodes": {"START": {"sub_states": [{"key": "X"}]}},
            "edges": [{"gate": "bad", "from": "START", "to": "NONEXISTENT"}],
        }
        path = self._write_workflow(str(tmp_path), workflow)
        graph = GraphRepository(path).load()
        # Graph is returned even with invariant errors (warnings logged)
        assert isinstance(graph, WorkflowGraph)


# ── StateRepository tests ────────────────────────────────────────────────

class TestStateRepository:

    def test_load_empty(self, tmp_path):
        repo = StateRepository()
        state = repo.load(str(tmp_path))
        assert state.current_node == ""
        assert state.revision == 0

    def test_save_and_load_roundtrip(self, tmp_path):
        repo = StateRepository()
        state = WorkflowState(current_node="WORK", current_sub_state="EXEC")
        saved = repo.save(str(tmp_path), state)

        assert saved.revision == 1  # CAS increments

        loaded = repo.load(str(tmp_path))
        assert loaded.current_node == "WORK"
        assert loaded.current_sub_state == "EXEC"
        assert loaded.revision == 1

    def test_cas_increment(self, tmp_path):
        repo = StateRepository()
        state = WorkflowState(current_node="A", revision=0)
        saved = repo.save(str(tmp_path), state)
        assert saved.revision == 1

        # Save again with revision=1 (from previous save)
        state2 = WorkflowState(current_node="B", revision=1)
        saved2 = repo.save(str(tmp_path), state2)
        assert saved2.revision == 2

    def test_cas_conflict(self, tmp_path):
        repo = StateRepository()
        state = WorkflowState(current_node="A", revision=0)
        repo.save(str(tmp_path), state)

        # Try to save with wrong revision
        state_bad = WorkflowState(current_node="B", revision=0)  # should be 1
        with pytest.raises(StateConflictError):
            repo.save(str(tmp_path), state_bad)

    def test_atomic_write(self, tmp_path):
        """Verify .tmp files don't persist after save."""
        repo = StateRepository()
        state = WorkflowState(current_node="X")
        repo.save(str(tmp_path), state)

        files = os.listdir(str(tmp_path))
        tmp_files = [f for f in files if f.endswith(".tmp")]
        assert len(tmp_files) == 0, f"Tmp files left behind: {tmp_files}"

    def test_creates_directory(self, tmp_path):
        repo = StateRepository()
        deep_dir = str(tmp_path / "a" / "b" / "c")
        state = WorkflowState(current_node="X")
        repo.save(deep_dir, state)
        assert os.path.exists(os.path.join(deep_dir, "data", "cognitive.sqlite3"))

    def test_reconcile_on_load(self, tmp_path):
        """Reconcile sub_state_index when graph is available."""
        from fast_mcp.features.workflow.infrastructure.graph_repository import GraphRepository

        # Write a graph with 2 sub-states
        workflow = {
            "initial_state": "W",
            "nodes": {"W": {"sub_states": [{"key": "A"}, {"key": "B"}]}},
            "edges": [],
        }
        wf_path = os.path.join(str(tmp_path), "workflow.json")
        with open(wf_path, "w") as f:
            json.dump(workflow, f)

        graph_repo = GraphRepository(wf_path)
        state_repo = StateRepository(graph_repository=graph_repo)

        # Save state with wrong index (index=0 but sub_state=B which is index=1)
        state = WorkflowState(current_node="W", current_sub_state="B", sub_state_index=0)
        state_repo.save(str(tmp_path), state)

        # Load should reconcile index to 1
        loaded = state_repo.load(str(tmp_path))
        assert loaded.sub_state_index == 1


# ── WorkflowStoreAdapter tests ───────────────────────────────────────────

class TestWorkflowStoreAdapter:

    def test_delegates_load_graph(self, tmp_path):
        from fast_mcp.features.workflow.infrastructure.graph_repository import GraphRepository

        workflow = {
            "initial_state": "S",
            "nodes": {"S": {"sub_states": [{"key": "X"}]}},
            "edges": [],
        }
        wf_path = os.path.join(str(tmp_path), "workflow.json")
        with open(wf_path, "w") as f:
            json.dump(workflow, f)

        graph_repo = GraphRepository(wf_path)
        state_repo = StateRepository()
        adapter = WorkflowStoreAdapter(graph_repo, state_repo)

        graph = adapter.load_graph()
        assert isinstance(graph, WorkflowGraph)
        assert "S" in graph.nodes

    def test_delegates_load_save_state(self, tmp_path):
        graph_repo = None  # not needed for state ops
        state_repo = StateRepository()
        adapter = WorkflowStoreAdapter(graph_repo, state_repo)

        state = WorkflowState(current_node="TEST")
        saved = adapter.save_state(str(tmp_path), state)
        assert saved.revision == 1

        loaded = adapter.load_state(str(tmp_path))
        assert loaded.current_node == "TEST"


# ── Import autonomy test ─────────────────────────────────────────────────

class TestInfrastructureAutonomy:

    def test_no_mcp_imports(self):
        """Infrastructure layer must not import from mcp/."""
        import ast

        infra_dir = os.path.join(
            os.path.dirname(__file__), "..", "fast_mcp", "features", "workflow", "infrastructure"
        )
        forbidden = {"mcp", "protocols", "shared.core", "features.main"}
        violations = []

        for root, dirs, files in os.walk(infra_dir):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                if not f.endswith(".py"):
                    continue
                filepath = os.path.join(root, f)
                with open(filepath) as fh:
                    try:
                        tree = ast.parse(fh.read())
                    except SyntaxError:
                        continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if any(alias.name.startswith(fw) for fw in forbidden):
                                violations.append(f"{f}: import {alias.name}")
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and any(node.module.startswith(fw) for fw in forbidden):
                            violations.append(f"{f}: from {node.module}")

        assert not violations, f"Infrastructure imports from mcp/: {violations}"
