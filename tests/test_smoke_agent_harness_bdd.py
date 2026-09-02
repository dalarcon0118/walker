"""tests/test_smoke_agent_harness_bdd.py — Smoke Capa 3: harness determinista.

Simula al AGENTE con reglas hardcodeadas (sin LLM): lee `status`, decide el
siguiente submit/transition según el sub-estado actual, e invoca tools MCP.
Valida el protocolo completo (JSON-RPC 2.0, dispatch, canal en Trace):

  - El flujo de workflow (submit plan_sprint) traza task_execute con
    channel="workflow" (lo ejecuta el motor).
  - Las tools públicas del agente (recall_knowledge) trazan task_execute con
    channel="mcp" (dispatch_public_task).

Determinista: no depende de un LLM; es el puente entre Capa 1 (unit) y un
smoke con agente IA real.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from features.delta_tracking import SpringService
from shared.core.engine.domain.entities import WorkflowState
from shared.core.engine.infrastructure.repositories import WorkflowStateRepository
from tests.conftest import register_project_in_global_registry

SPRINT_GOAL = "Implementar modulo de autenticacion completo"


@pytest.fixture
def _smoke_project(tmp_path):
    """Registra el proyecto (reversible) y monta el proyecto cognitivo."""
    tmp = str(tmp_path)
    reg_ctx = register_project_in_global_registry(tmp).__enter__()
    try:
        yield _setup_project(tmp)
    finally:
        reg_ctx.__exit__(None, None, None)
        from shared.core.engine.bootstrap import reset_module_registry

        reset_module_registry()
        from protocols.mcp import mcp_adapter

        mcp_adapter._SERVER = None


def _setup_project(tmp_path: str) -> str:
    cog = Path(tmp_path) / ".cognitive" / "projects" / "walker"
    cog.mkdir(parents=True)
    (cog / "data").mkdir()
    (cog / "data" / "project.json").write_text(json.dumps({
        "project_id": "walker", "name": "walker", "repo_path": tmp_path,
        "language": "python", "framework": "unknown", "status": "active",
    }))
    from features.delta_tracking.infrastructure.cognitive_delta_repo import (
        CognitiveDeltaRepository,
    )

    CognitiveDeltaRepository(str(cog))
    svc = SpringService(str(cog))
    svc.create_spring(
        "sprint-smoke", goal="Smoke sprint", status="active",
        project_id="walker", session_id="s1",
    )
    sprint_dir = cog / "data" / "spring" / "sprint-smoke"
    sprint_dir.mkdir(parents=True, exist_ok=True)
    wf = WorkflowState(current_node="SPRINT", current_sub_state="PLANNING")
    wf.context["chain_dir"] = str(sprint_dir)
    wf.context["project_id"] = "walker"
    wf.context["session_id"] = "s1"
    WorkflowStateRepository().save(str(sprint_dir), wf)
    return str(sprint_dir)


def _mcp_call(name: str, arguments: dict) -> dict:
    from protocols.mcp.mcp_adapter import _handle_request

    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    return _handle_request(msg)


def _parse(resp: dict) -> dict:
    content = resp.get("result", {}).get("content", [])
    text = content[0].get("text", "") if content else ""
    return json.loads(text) if text.startswith("{") else {}


class DeterministicAgent:
    """Harness que decide el siguiente paso por reglas (simula al agente)."""

    def __init__(self, sprint_dir: str):
        self.sprint_dir = sprint_dir

    def current_sub_state(self) -> str:
        return WorkflowStateRepository().load(self.sprint_dir).current_sub_state

    def current_node(self) -> str:
        return WorkflowStateRepository().load(self.sprint_dir).current_node

    def _task_id(self) -> str:
        from features.delta_tracking import SpringService

        svc = SpringService(str(Path(self.sprint_dir).parent.parent.parent))
        specs = svc.get_specs("sprint-smoke")
        return specs[0]["spec_id"] if specs else "TASK-001"

    def step(self) -> dict:
        """Ejecuta la acción según el sub-estado actual (reglas hardcodeadas).

        Recorre SPRINT → DESIGN → ANALYZE → IMPLEMENT con tasks REALES en los
        sub-estados con action (plan_sprint, spec_task, gather_context) y
        gates reales entre nodos (start_task, design_approved,
        analysis_approved). Los sub-estados llm_task de contenido (sin action)
        reciben su payload de schema para avanzar.
        """
        node = self.current_node()
        sub = self.current_sub_state()

        # ── SPRINT ──────────────────────────────────────────────
        if node == "SPRINT" and sub == "PLANNING":
            return _mcp_call("submit", {"project_id": "walker", "body": {
                "goal": SPRINT_GOAL,
                "tasks": [{"objective": "Login screen", "type": "feature", "domain": "frontend"}],
            }})
        if node == "SPRINT" and sub == "PLAN_APPROVAL":
            # El agente aprueba el plan y refiere el task_id de la spec.
            return _mcp_call("submit", {"project_id": "walker", "body": {
                "decision": "approve", "task_id": self._task_id(),
            }})
        if node == "SPRINT" and sub == "TASK_SPEC":
            # spec_task real — payload completo del input_schema.
            return _mcp_call("submit", {"project_id": "walker", "body": {
                "task_id": self._task_id(),
                "status": "specified",
                "approval_type": "decision",
                "result_state": "implementar login",
                "task_description": "Crear pantalla de login",
                "predicates": ["login funciona"],
                "files": ["src/auth.py"],
                "preconditions": ["auth service disponible"],
                "acceptance_criteria": ["usuario puede loguearse"],
                "quality_gates": ["tests pasan"],
                "decision_data": {
                    "decision": "usar API", "architecture": "monolito",
                    "implementation": "1 svc",
                },
            }})
        if node == "SPRINT" and sub == "TASK_REVIEW":
            return _mcp_call("submit", {"project_id": "walker", "body": {
                "decision": "approve", "approval_type": "decision",
            }})
        if node == "SPRINT" and sub == "ACTIVE":
            # Gate real: SPRINT.ACTIVE → DESIGN.DESIGN_GATHER
            return _mcp_call("transition", {"project_id": "walker", "gate": "start_task"})

        # ── DESIGN ───────────────────────────────────────────────
        if node == "DESIGN" and sub == "DESIGN_GATHER":
            # gather_context real (auto). El forwarding (SPEC-013) exige que el
            # submit incluya los campos del DESTINO (DESIGN_ANALYZE: q1..q3/a1..a3)
            # para auto-avanzar — si no, el engine espera input en el destino.
            return _mcp_call("submit", {"project_id": "walker", "body": {
                "q1": "capa principal", "a1": "aplicacion",
                "q2": "capa datos", "a2": "repos",
                "q3": "capa interfaz", "a3": "frontend",
            }})
        if node == "DESIGN" and sub == "DESIGN_ANALYZE":
            return _mcp_call("submit", {"project_id": "walker", "body": {
                "q1": "capa principal", "a1": "la aplicacion usa capas claras",
                "q2": "capa datos", "a2": "los repositorios persisten datos",
                "q3": "capa interfaz", "a3": "el frontend consume la api",
            }})
        if node == "DESIGN" and sub == "DESIGN_REVIEW":
            return _mcp_call("submit", {"project_id": "walker", "body": {"decision": "approve"}})
        if node == "DESIGN" and sub == "CKG_SAVE":
            # save_to_ckg real — al terminar el nodo, la gate design_approved cruza.
            return _mcp_call("submit", {"project_id": "walker", "body": {
                "actualizaciones": [], "nuevas_reglas": [], "nuevas_advertencias": [],
            }})
        if node == "DESIGN" and sub == "DESIGN_REVIEW":
            return _mcp_call("transition", {"project_id": "walker", "gate": "design_approved"})

        # ── ANALYZE ──────────────────────────────────────────────
        if node == "ANALYZE" and sub == "GATHER":
            # Forwarding al destino ANALYZE (q1..q3/a1..a3 con minLength 10)
            return _mcp_call("submit", {"project_id": "walker", "body": {
                "q1": "que componentes hay", "a1": "hay tres componentes principales",
                "q2": "cual impacto", "a2": "el impacto es en el modulo auth",
                "q3": "que enfoque", "a3": "se recomienda el patron estrategia",
            }})
        if node == "ANALYZE" and sub == "ANALYZE":
            return _mcp_call("submit", {"project_id": "walker", "body": {
                "q1": "que componentes hay", "a1": "hay tres componentes principales",
                "q2": "cual impacto", "a2": "el impacto es en el modulo auth",
                "q3": "que enfoque", "a3": "se recomienda el patron estrategia",
            }})
        if node == "ANALYZE" and sub == "REVIEW":
            return _mcp_call("submit", {"project_id": "walker", "body": {"decision": "approve"}})
        if node == "ANALYZE" and sub == "KNOWLEDGE_CAPTURE":
            # Al terminar el nodo ANALYZE, la gate analysis_approved cruza.
            return _mcp_call("transition", {"project_id": "walker", "gate": "analysis_approved"})

        # ── IMPLEMENT (primer sub-estado alcanzado) ─────────────
        if node == "IMPLEMENT" and sub == "USER_GATE":
            return _mcp_call("submit", {"project_id": "walker", "body": {"decision": "proceed"}})

        return {"noop": True, "at": f"{node}.{sub}"}


@pytest.mark.xfail(
    reason="Legacy mcp/ test — will be superseded by Sprint 5 fast_mcp/ port",
    strict=False,
)
class TestSmokeAgentHarness:
    """Capa 3: el harness recorre el flujo y valida canales en Trace."""

    def test_harness_recorre_planning_hasta_task_spec(self, tmp_path, _smoke_project):
        sprint_dir = _smoke_project
        agent = DeterministicAgent(sprint_dir)

        # Paso 1: PLANNING → plan_sprint (submit vía MCP)
        r1 = agent.step()
        assert r1.get("result", {}).get("isError") is not True
        assert agent.current_sub_state() == "PLAN_APPROVAL"

        # Paso 2: PLAN_APPROVAL → approve + transition → TASK_SPEC
        r2 = agent.step()
        assert r2.get("result", {}).get("isError") is not True
        assert agent.current_sub_state() == "TASK_SPEC"

        # Delta: la spec creada por plan_sprint real
        svc = SpringService(str(Path(str(tmp_path)) / ".cognitive" / "projects" / "walker"))
        assert svc.get_specs("sprint-smoke"), "specs no creadas en delta"

    def test_harness_trace_tiene_canal_workflow_y_mcp(self, tmp_path, _smoke_project):
        """Trace: submit del workflow → channel=workflow; tool MCP → channel=mcp."""
        sprint_dir = _smoke_project
        agent = DeterministicAgent(sprint_dir)

        # 1. Flujo de workflow (submit) — traza channel=workflow
        agent.step()
        # 2. Tool pública del agente (recall_knowledge) — traza channel=mcp
        _mcp_call("recall_knowledge", {"_project_id": "walker", "query": "Login screen", "index": "ckg"})

        from features.event_tracing import search

        events = search(sprint_dir, {})
        task_events = [e for e in events if e.get("event_type") == "task_execute"]
        assert task_events, "sin task_execute en Trace"

        channels = {(e.get("data") or {}).get("channel") for e in task_events}
        print(f"\nCHANNELS en task_execute: {sorted(c for c in channels if c)}")
        # El submit del workflow traza channel=workflow (ctx del engine)
        assert "workflow" in channels, (
            f"submit no trazó channel=workflow: {sorted(channels)}"
        )
        # recall_knowledge vía MCP traza channel=mcp (dispatch_public_task)
        assert "mcp" in channels, (
            f"recall_knowledge vía MCP no trazó channel=mcp: {sorted(channels)}"
        )

    def test_harness_recorre_implement(self, tmp_path, _smoke_project):
        """Capa 3 extendida: el harness recorre SPRINT→DESIGN→ANALYZE→IMPLEMENT.

        Valida que el flujo MCP completo cruce los nodos con GATES reales
        (start_task, design_approved, analysis_approved) y ejecute tasks
        reales donde hay action (plan_sprint, spec_task, gather_context).
        """
        sprint_dir = _smoke_project
        agent = DeterministicAgent(sprint_dir)
        visited = set()
        r = None
        for _ in range(40):
            state_key = f"{agent.current_node()}.{agent.current_sub_state()}"
            if state_key in visited:
                # Loop sin avance: el harness no cubre este sub-estado.
                raise AssertionError(f"loop en {state_key} — harness incompleto (última resp: {r})")
            visited.add(state_key)
            r = agent.step()
            assert r.get("result", {}).get("isError") is not True, (
                f"step falló en {state_key}: {r}"
            )
            if agent.current_node() == "IMPLEMENT":
                break

        # El flujo llegó a IMPLEMENT (cruzó SPRINT→DESIGN→ANALYZE con gates reales)
        assert agent.current_node() == "IMPLEMENT", (
            f"no llegó a IMPLEMENT: {agent.current_node()}.{agent.current_sub_state()}"
        )

        # Tasks reales ejecutadas en el camino (Trace con task_execute)
        from features.event_tracing import search

        events = search(sprint_dir, {})
        task_events = [e for e in events if e.get("event_type") == "task_execute"]
        executed = {
            (e.get("data") or {}).get("task")
            for e in task_events
            if (e.get("data") or {}).get("task")
        }
        print(f"\nTASKS reales ejecutadas: {sorted(executed)}")
        # plan_sprint y gather_context son actions reales del flujo
        assert "plan_sprint" in executed, f"plan_sprint no ejecutada: {sorted(executed)}"
        assert "gather_context" in executed, (
            f"gather_context no ejecutada en DESIGN: {sorted(executed)}"
        )
