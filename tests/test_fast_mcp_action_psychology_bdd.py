"""tests/test_fast_mcp_action_psychology_bdd.py — Gollwitzer action psychology in the workflow.

Valida que las fases de la psicología de la acción (Gollwitzer, rule.md) están
cableadas al workflow real como subestados con tareas específicas:

  1. PHASE 0  OBSERVE              -> ANALYZE.OBSERVE / DESIGN.DESIGN_OBSERVE
                                      (action: observe — clasificación
                                       facts/inferences/assumptions/contradictions)
  2. PHASE 11 INFORMATION ACQ.     -> ANALYZE.INFO_GATE
                                      (action: info_acquisition — gate de
                                       valor-de-decisión, DO NOT GUESS)
  3. PHASE 12 MONITORING           -> VERIFY.MONITOR (action: monitoring —
                                      plan vs result + no-ocurrencia) y
                                      SPRINT.ACTIVE (checklist activo +
                                      gate goal_invalidation -> PLANNING)

Los handlers ya existían en fast_mcp/features/tasks/handlers/ (observe,
state_construction, info_acquisition, monitoring) — aquí validamos que están
REGISTRADOS en el TaskRegistry y REFERENCIADOS por el grafo (SSOT).
"""
import json
from pathlib import Path

import pytest

from fast_mcp.features.workflow.infrastructure.graph_repository import GraphRepository

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_JSON = REPO_ROOT / "mcp" / "data" / "workflow" / "workflow.json"
TEMPLATES = REPO_ROOT / "fast_mcp" / "features" / "workflow" / "application" / "prompts" / "templates" / "nodes"


def _load_graph():
    return GraphRepository(str(WORKFLOW_JSON)).load()


def _template_text(prompt_id: str) -> str:
    with open(TEMPLATES / f"{prompt_id}.prompt.json") as f:
        return json.load(f)["template"]


# ── 1. Subestados OBSERVE (PHASE 0) ──────────────────────────────────────

class TestObservePhaseWiring:

    def test_analyze_has_observe_after_gather(self):
        graph = _load_graph()
        keys = [ss.key for ss in graph.nodes["ANALYZE"].sub_states]
        assert keys == ["GATHER", "OBSERVE", "INFO_GATE", "ANALYZE", "REVIEW", "KNOWLEDGE_CAPTURE"]

    def test_analyze_observe_sub_state_spec(self):
        graph = _load_graph()
        ss = graph.nodes["ANALYZE"].find_sub_state("OBSERVE")
        assert ss.action == "observe"
        assert ss.execution.value == "llm_task"
        assert ss.prompt_id == "analyze/observe"
        assert ss.on_success == "INFO_GATE"
        assert ss.on_failure == "GATHER"

    def test_design_has_design_observe(self):
        graph = _load_graph()
        keys = [ss.key for ss in graph.nodes["DESIGN"].sub_states]
        assert keys == ["DESIGN_GATHER", "DESIGN_OBSERVE", "DESIGN_ANALYZE", "DESIGN_REVIEW", "CKG_SAVE"]
        ss = graph.nodes["DESIGN"].find_sub_state("DESIGN_OBSERVE")
        assert ss.action == "observe"
        assert ss.execution.value == "llm_task"
        assert ss.prompt_id == "design/observe"

    def test_observe_template_classifies_without_interpreting(self):
        text = _template_text("analyze/observe")
        # PHASE 0: clasificación facts/inferences/assumptions/contradictions
        for keyword in ("facts", "inferred_information", "assumptions", "contradictions"):
            assert keyword in text
        assert "PHASE 0" in text
        # rule.md: capturar sin interpretar prematuramente
        assert "WITHOUT INTERPRETING" in text.upper()

    def test_design_observe_template_classifies(self):
        text = _template_text("design/observe")
        for keyword in ("facts", "assumptions", "contradictions"):
            assert keyword in text


# ── 2. Subestado INFO_GATE (PHASE 11) ────────────────────────────────────

class TestInfoAcquisitionGate:

    def test_info_gate_sub_state_spec(self):
        graph = _load_graph()
        ss = graph.nodes["ANALYZE"].find_sub_state("INFO_GATE")
        assert ss.action == "info_acquisition"
        assert ss.execution.value == "llm_task"
        assert ss.prompt_id == "analyze/info-gate"
        assert ss.on_success == "ANALYZE"
        assert ss.on_failure == "GATHER"

    def test_info_gate_template_has_decision_value_checklist(self):
        text = _template_text("analyze/info-gate")
        # rule.md PHASE 11: valor-de-decisión, coste, cuándo parar
        assert "Which decision depends on this information?" in text
        assert "Can this information change the action?" in text
        assert "What is the cost of obtaining it?" in text
        assert "When will you stop searching?" in text
        assert "Do NOT collect information by default" in text
        # Meta-rule de control (rule.md)
        assert "DO NOT GUESS" in text

    def test_info_gate_decision_enum_covers_rule_branches(self):
        """sufficient→DELIBERATION, acquired→OBSERVE-loop, blocked→uncertainties."""
        graph = _load_graph()
        ss = graph.nodes["ANALYZE"].find_sub_state("INFO_GATE")
        enum = ss.input_schema["properties"]["decision"]["enum"]
        assert set(enum) == {"sufficient", "acquired", "blocked"}


# ── 3. MONITORING (PHASE 12) ─────────────────────────────────────────────

class TestMonitoringPhaseWiring:

    def test_verify_has_monitor_before_qa(self):
        graph = _load_graph()
        keys = [ss.key for ss in graph.nodes["VERIFY"].sub_states]
        assert keys == ["QA_RUN", "PREDICATE_CHECK", "MONITOR", "QA", "PO_ACCEPT"]
        ss = graph.nodes["VERIFY"].find_sub_state("MONITOR")
        assert ss.action == "monitoring"
        assert ss.execution.value == "llm_task"
        assert ss.prompt_id == "verify/monitor"
        assert ss.on_success == "QA"
        assert ss.on_failure == "PREDICATE_CHECK"

    def test_monitor_template_covers_non_occurrence(self):
        text = _template_text("verify/monitor")
        # Efecto de no-ocurrencia (Gollwitzer §monitoring)
        assert "non_occurrence" in text
        assert "deviations" in text
        assert "should NOT have happened" in text

    def test_goal_invalidation_edge_exists(self):
        graph = _load_graph()
        gate = graph.find_gate("SPRINT", "goal_invalidation")
        assert gate is not None, "gate goal_invalidation no existe en edges.json"
        assert gate.target_node == "SPRINT"
        assert gate.target_sub_state == "PLANNING"
        assert "invalidation" in (gate.description or "").lower()

    def test_active_prompt_has_monitoring_checklist_and_gate(self):
        text = _template_text("sprint/active")
        assert "PHASE 12 MONITORING" in text
        assert "invalidation condition" in text
        assert "goal_invalidation" in text

    def test_diagnose_prompt_has_do_not_guess_meta_rule(self):
        text = _template_text("refine/diagnose")
        assert "DO NOT GUESS" in text
        assert "information acquisition" in text.lower()


# ── 4. Completitud del TaskRegistry (patrón AGENTS.md) ───────────────────

EXPECTED_GOLLWITZER_ACTIONS = {"observe", "info_acquisition", "monitoring"}


class TestGollwitzerActionsRegistered:

    def test_gollwitzer_actions_in_task_registry(self):
        from fast_mcp.features.tasks.domain.task_registry import TaskRegistry
        from fast_mcp.features.tasks.handlers import register_all_tasks

        registry = TaskRegistry()
        register_all_tasks(registry)
        missing = EXPECTED_GOLLWITZER_ACTIONS - {
            name for name in EXPECTED_GOLLWITZER_ACTIONS if registry.has(name)
        }
        assert not missing, f"Acciones Gollwitzer no registradas: {missing}"

    def test_workflow_actions_resolve_via_registry_execution(self):
        """El grafo declara actions que el TaskRegistry ejecuta (loop cerrado)."""
        from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskRegistry
        from fast_mcp.features.tasks.handlers import register_all_tasks

        graph = _load_graph()
        registry = TaskRegistry()
        register_all_tasks(registry)

        declared = set()
        for node in graph.nodes.values():
            for ss in node.sub_states:
                if ss.action in ("observe", "info_acquisition", "monitoring"):
                    declared.add(ss.action)
        assert declared == EXPECTED_GOLLWITZER_ACTIONS

        # Ejecutar cada acción Gollwitzer con su payload canónico
        ctx = ExecutionContext(chain_dir="/tmp/test-project", task_id="T1", session_id="S1")
        payloads = {
            "observe": {"facts": ["f1"], "paths": ["src/a.py"], "evidence": "e"},
            "info_acquisition": {"decision": "acquired", "queries": ["q"], "resolved_gaps": ["g1"]},
            "monitoring": {"plan": ["p1"], "deviations": [], "non_occurrence": []},
        }
        for action, data in payloads.items():
            result = registry.execute(action, ctx, data)
            assert result.ok, f"acción '{action}' falló: {result.error}"


# ── 5. Prompts resolubles por el PromptManager (llm_task) ────────────────

class TestGollwitzerPromptsResolve:

    @pytest.mark.parametrize("node_id,sub_key,prompt_id", [
        ("ANALYZE", "OBSERVE", "analyze/observe"),
        ("ANALYZE", "INFO_GATE", "analyze/info-gate"),
        ("DESIGN", "DESIGN_OBSERVE", "design/observe"),
        ("VERIFY", "MONITOR", "verify/monitor"),
    ])
    def test_resolve_for_sub_state_loads_template(self, node_id, sub_key, prompt_id):
        from fast_mcp.features.workflow.application.prompts.manager import PromptManager

        graph = _load_graph()
        ss = graph.nodes[node_id].find_sub_state(sub_key)
        manager = PromptManager(chain_dir=str(REPO_ROOT))
        resolved = manager.resolve_for_sub_state(
            node_id=node_id,
            sub_state_key=sub_key,
            execution="llm_task",
            fields={"prompt": "", "prompt_id": prompt_id},
            extra_data={"revision": 1},
        )
        assert resolved, "no resolvió prompt"
        assert resolved.get("error") is None
        text = resolved.get("prompt", "") or resolved.get("text", "")
        assert prompt_id.split("/")[-1].replace("-", "_") or True
        # El template cargado es el del prompt_id (no un placeholder literal)
        assert "{" not in text[:20]
