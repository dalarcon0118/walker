"""BDD: un hecho, un dueño — y una sola vez en el prompt.

`_dedupe_knowledge` ya limpia repeticiones ENTRE secciones de conocimiento. Lo
que faltaba blindar es la repetición CRUZADA: una decisión puede llegar por
`decisions_list` (prompt_context, scope de la tarea) y también por
`knowledge_global`/`knowledge_relevant`/`project_patterns` (manager). Hoy da
cero en los 50 sub-estados; este test lo deja así.

Medido 2026-11-18: cero duplicados cruzados con el estado vivo. Si aparece uno,
el prompt está pagando dos veces por el mismo hecho y el modelo lo lee como dos
señales distintas (context rot: el distractor repetido degrada).
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from fast_mcp.features.workflow.context import WorkflowContext
from fast_mcp.features.workflow.domain.execution import ExecutionContext

REPO_ROOT = "/Users/davidmartinez/develop/Active-projects/walker"
KNOWLEDGE_KEYS = (
    "knowledge_global",
    "knowledge_relevant",
    "knowledge_architecture",
    "project_patterns",
    "project_architecture",
    "project_quality_gates",
)


def _signature(line: str) -> str:
    return " ".join(line.strip().lstrip("-•").strip().lower().split())


def _lines(text) -> set[str]:
    return {_signature(line) for line in str(text or "").splitlines() if line.strip()}


@pytest.fixture(scope="module")
def rendered_data():
    ctx = WorkflowContext.resolve(REPO_ROOT)
    if ctx is None:
        pytest.skip("workflow no resoluble")
    orchestrator = ctx.orchestrator
    state = orchestrator._load_state()
    graph = orchestrator._load_graph()
    if state is None or graph is None:
        pytest.skip("sin estado/grafo")
    exec_ctx = ExecutionContext.create(
        chain_dir=REPO_ROOT, session_id="roadmap-session-1"
    )
    # Cuatro posiciones alcanzan: la duplicación depende de los TIPOS de dato
    # (decisión/convención/patrón), no del sub-estado. Renderizar los 50
    # costaba 17s por reconstruir el servicio de sprint en cada uno.
    positions = (
        ("DESIGN", "DESIGN_REVIEW"),
        ("IMPLEMENT", "WORK"),
        ("VERIFY", "QA"),
        ("SPRINT", "TASK_REVIEW"),
    )
    out = []
    for node_id, sub_key in positions:
        node = graph.get_node(node_id)
        if node is None:
            continue
        index = next((i for i, ss in enumerate(node.sub_states) if ss.key == sub_key), None)
        if index is None:
            continue
        probe = replace(
            state, current_node=node_id, current_sub_state=sub_key, sub_state_index=index
        )
        resolved = orchestrator._resolve_current_prompt(probe, graph, exec_ctx) or {}
        data = resolved.get("data") or {}
        if data:
            out.append((f"{node_id}.{sub_key}", data))
    return out


def test_ninguna_decision_se_repite_en_el_conocimiento(rendered_data):
    offenders = []
    for position, data in rendered_data:
        decisions = _lines(data.get("decisions_list"))
        if not decisions:
            continue
        for key in KNOWLEDGE_KEYS:
            repeated = decisions & _lines(data.get(key))
            if repeated:
                offenders.append(f"{position} · {key}: {sorted(repeated)[:2]}")

    assert offenders == [], (
        "hechos repetidos entre decisions_list y conocimiento:\n  " + "\n  ".join(offenders)
    )


def test_ninguna_linea_se_repite_entre_secciones(rendered_data):
    offenders = []
    for position, data in rendered_data:
        seen: dict[str, str] = {}
        for key in KNOWLEDGE_KEYS:
            for line in _lines(data.get(key)):
                if line in seen:
                    offenders.append(f"{position}: '{line[:60]}' en {seen[line]} y {key}")
                else:
                    seen[line] = key

    assert offenders == [], "conocimiento repetido entre secciones:\n  " + "\n  ".join(offenders)
