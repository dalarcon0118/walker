"""test_baseline_preflight_bdd.py — punto A y punto B automáticos.

El baseline no puede depender de que el agente se acuerde de pedirlo (medido: la
inyección se ignora). Va enganchado al motor como sub-estado `auto`:

    ANALYZE:  GATHER → BASELINE → OBSERVE ...
    VERIFY:   QA_RUN → PREDICATE_CHECK → BASELINE_DIFF → MONITOR ...

Estos tests fijan el contrato de ese enganche: los nodos declaran las acciones y
el label del diff coincide con el del capture (el orquestador rellena
`task_id` con el NOMBRE DE LA ACCIÓN, así que usarlo rompía la pareja).
"""

from __future__ import annotations

import json
from pathlib import Path

from fast_mcp.features.tasks.handlers.baseline import _label_for

NODES = Path(__file__).resolve().parent.parent / "data" / "workflow" / "nodes"


def _node(name: str) -> dict:
    return json.loads((NODES / f"{name}.node.json").read_text())


def _ctx(task_id: str = ""):
    from types import SimpleNamespace

    return SimpleNamespace(chain_dir="/tmp/x", task_id=task_id, session_id="s")


def test_analyze_tiene_baseline_auto_entre_gather_y_observe():
    subs = _node("analyze")["sub_states"]
    keys = [s["key"] for s in subs]
    baseline = subs[keys.index("BASELINE")]

    assert keys.index("GATHER") < keys.index("BASELINE") < keys.index("OBSERVE")
    assert baseline["execution"] == "auto"
    assert baseline["action"] == "capture_baseline"
    assert subs[keys.index("GATHER")]["on_success"] == "BASELINE"
    assert baseline["on_success"] == "OBSERVE"


def test_verify_tiene_diff_auto_despues_de_predicados():
    subs = _node("verify")["sub_states"]
    keys = [s["key"] for s in subs]
    diff = subs[keys.index("BASELINE_DIFF")]

    assert keys.index("PREDICATE_CHECK") < keys.index("BASELINE_DIFF") < keys.index("MONITOR")
    assert diff["execution"] == "auto"
    assert diff["action"] == "diff_baseline"


def test_label_ignora_el_task_id_que_es_el_nombre_de_la_accion():
    """capture y diff tienen que compartir label, si no el diff no encuentra base."""
    assert _label_for({"task_id": "capture_baseline"}, _ctx()) == "task-actual"
    assert _label_for({"task_id": "diff_baseline"}, _ctx()) == "task-actual"


def test_label_explicito_gana():
    assert _label_for({"label": "sprint-7"}, _ctx()) == "sprint-7"


def test_las_acciones_estan_en_el_registry():
    from fast_mcp.features.tasks.domain.task_registry import TaskRegistry
    from fast_mcp.features.tasks.handlers import _register_module_core

    registry = TaskRegistry()
    _register_module_core("baseline", registry)

    assert registry.has("capture_baseline")
    assert registry.has("diff_baseline")
