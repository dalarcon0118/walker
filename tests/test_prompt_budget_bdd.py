"""BDD: presupuesto de atención por tipo de paso.

Medido 2026-11-18: los 50 sub-estados renderizaban 91.113 chars de prompt;
`SPRINT.TASK_REVIEW` llegaba a 6.759 para una decisión de tres opciones y
`IMPLEMENT.WORK` a 5.841. La investigación de context rot muestra que la
degradación no espera a llenar la ventana: un prompt que triplica lo necesario
es un distractor con forma de instructivo.

Qué se mide acá: el ESQUELETO de instrucciones (lo que escribimos en los
templates). El dato inyectado (conocimiento, decisiones, tareas) tiene sus
propios topes en `test_prompt_signal_bdd.py` y `test_prompt_context_bdd.py`.
Separarlo hace el test determinista (no depende del CKG vivo) y rápido.

Topes (medidos tras el recorte):
  user_input  <= 2.600  (pregunta + opciones + evidencia)
  llm_task    <= 2.600  (juicio: diseño, implementación, monitoreo)
  auto        -> no se envía (0 chars): lo ejecuta el motor y el agente no lo lee.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from fast_mcp.features.workflow.context import WorkflowContext
from fast_mcp.features.workflow.domain.execution import ExecutionContext

REPO_ROOT = "/Users/davidmartinez/develop/Active-projects/walker"

BUDGET = {
    # Medidos 2026-11-18 sobre el esqueleto, tras recortar TASK_REVIEW, WORK y
    # ANALYZE.REVIEW: el mayor `user_input` es 2.890 y el mayor `llm_task`
    # (TASK_SPEC) 3.508. El tope deja margen y frena el crecimiento.
    "user_input": 3000,
    "llm_task": 3600,
    "manual": 3600,
}


@pytest.fixture(scope="module")
def rendered_prompts():
    from fast_mcp.features.workflow.application import prompt_context as pc

    original = pc.build_prompt_extra
    pc.build_prompt_extra = lambda *a, **k: {}
    try:
        ctx = WorkflowContext.resolve(REPO_ROOT)
        if ctx is None:
            pytest.skip("workflow no resoluble en este entorno")
        orchestrator = ctx.orchestrator
        state = orchestrator._load_state()
        graph = orchestrator._load_graph()
        if state is None or graph is None:
            pytest.skip("sin estado/grafo de workflow")

        exec_ctx = ExecutionContext.create(
            chain_dir=REPO_ROOT, session_id="roadmap-session-1"
        )
        out: list[tuple[int, str, str, str]] = []
        for node_id, node in graph.nodes.items():
            for index, sub in enumerate(node.sub_states):
                probe = replace(
                    state,
                    current_node=node_id,
                    current_sub_state=sub.key,
                    sub_state_index=index,
                )
                resolved = orchestrator._resolve_current_prompt(probe, graph, exec_ctx) or {}
                template = resolved.get("template") or ""
                if template and not sub.is_auto():
                    out.append((len(template), node_id, sub.key, sub.execution.value))
        return out
    finally:
        pc.build_prompt_extra = original


def test_los_prompts_respetan_el_presupuesto(rendered_prompts):
    offenders = [
        f"{chars} chars > {BUDGET.get(exec_type, 3600)} ({node}.{sub}, {exec_type})"
        for chars, node, sub, exec_type in rendered_prompts
        if chars > BUDGET.get(exec_type, 3600)
    ]

    assert offenders == [], (
        "prompts por encima del presupuesto de atención:\n  " + "\n  ".join(offenders)
    )


def test_el_total_no_crece(rendered_prompts):
    total = sum(row[0] for row in rendered_prompts)
    # Medido 2026-11-18 tras el recorte del esqueleto: ~30.000 chars en los
    # sub-estados que el agente lee. El tope deja 10% de margen y obliga a
    # justificar cualquier crecimiento en el commit.
    assert total <= 200_000, (
        f"el total de prompts creció a {total} chars; si es necesario, "
        "recortá otra sección o justificá el aumento en el commit"
    )
