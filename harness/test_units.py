"""test_units.py — tests del harness que no corren agentes.

    python3 -m pytest harness/test_units.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from runner import (  # noqa: E402
    detect_project,
    eval_knowledge_nodes,
    model_label,
    session_usage,
    summary_knowledge_nodes,
    test_command as build_test_command,
)


def test_model_label_normaliza_provider_y_id():
    """El CSV debe registrar el modelo REAL de la sesión, no "(config)".

    Regresión medida 2026-11-16: el baseline pinneó deepseek-v4.1-flash y el
    rerun corrió con el default de la config; el CSV decía "(config)" y los
    tokens (1,9x) parecían efecto del código cuando era otro modelo.
    """
    raw = '{"id":"deepseek-v4.1-flash","providerID":"opencode-go"}'
    assert model_label(raw) == "opencode-go/deepseek-v4.1-flash"


def test_model_label_acepta_dict_id_solo_y_vacio():
    assert model_label({"id": "x", "providerID": "p"}) == "p/x"
    assert model_label({"modelID": "m"}) == "m"
    assert model_label(None) is None
    assert model_label("") is None


def test_session_usage_siempre_devuelve_las_mismas_claves():
    """Incluso sin sesión o sin DB: contrato estable para el CSV."""
    usage = session_usage(None)
    assert set(usage) == {"tokens_in", "tokens_out", "cost_usd", "model"}
    assert usage == {"tokens_in": None, "tokens_out": None, "cost_usd": None, "model": None}


def test_agent_default_model_lee_frontmatter_y_json(tmp_path):
    """El modelo efectivo de `--agent X` vive en el frontmatter del agente.

    Sin resolverlo, un run sin --model se registraba como "(config)" y no había
    forma de saber que corría con otro modelo.
    """
    from runner import agent_default_model

    (tmp_path / "build.md").write_text("---\nmodel: opencode/beam-free\ntools: {}\n---\n\n# Build\n")
    assert agent_default_model("build", tmp_path) == "opencode/beam-free"

    (tmp_path / "plan.json").write_text('{"model": "opencode-go/deepseek-v4.1-flash"}')
    assert agent_default_model("plan", tmp_path) == "opencode-go/deepseek-v4.1-flash"

    (tmp_path / "sinmodelo.md").write_text("---\ntools: {}\n---\n")
    assert agent_default_model("sinmodelo", tmp_path) is None
    assert agent_default_model("no-existe", tmp_path) is None


def test_summarize_agrega_e_ignora_none():
    """El resumen es la señal: totales y mediana, sin que un None rompa nada."""
    from runner import summarize

    rows = [
        {"pass": 1, "walker_gate": 1, "tokens_in": 100, "tokens_out": 10,
         "duration_s": 10.0, "model": "m"},
        {"pass": 1, "walker_gate": 1, "tokens_in": 300, "tokens_out": 30,
         "duration_s": 30.0, "model": "m"},
        {"pass": 0, "walker_gate": 0, "tokens_in": None, "tokens_out": None,
         "duration_s": 20.0, "model": "m"},
    ]
    s = summarize(rows, "run-x", wall_s=99.0)

    assert s["tokens_in"] == {"total": 400.0, "median": 200.0, "min": 100.0, "max": 300.0}
    assert s["duration_s"]["median"] == 20.0
    assert (s["pass"], s["gate"], s["tasks"]) == (2, 2, 3)
    assert s["model_comparable"] is True
    assert s["wall_s"] == 99.0


def test_summarize_marca_modelos_mezclados():
    """Si la corrida mezcla modelos, el resumen lo grita (tokens no comparables)."""
    from runner import summarize

    rows = [
        {"pass": 1, "walker_gate": 1, "tokens_in": 1, "tokens_out": 1,
         "duration_s": 1.0, "model": "a/x"},
        {"pass": 1, "walker_gate": 1, "tokens_in": 2, "tokens_out": 2,
         "duration_s": 2.0, "model": "b/y"},
    ]
    s = summarize(rows, "run-y", wall_s=3.0)
    assert s["model_comparable"] is False
    assert s["models"] == ["a/x", "b/y"]


def test_summarize_sin_gate_no_reporta_fallos():
    """Sin --walker-gate los rows traen walker_gate=None: eso es 'n/a', no '0/1'."""
    from runner import summarize

    rows = [{"pass": 1, "walker_gate": None, "tokens_in": 10, "tokens_out": 1,
             "duration_s": 5.0, "model": "m"}]
    s = summarize(rows, "run-z", wall_s=6.0)
    assert s["gate_requested"] is False
    assert s["gate"] == 0


def test_summarize_con_gate_cuenta_los_veredictos():
    from runner import summarize

    rows = [
        {"pass": 1, "walker_gate": 1, "tokens_in": 10, "tokens_out": 1,
         "duration_s": 5.0, "model": "m"},
        {"pass": 1, "walker_gate": 0, "tokens_in": 10, "tokens_out": 1,
         "duration_s": 5.0, "model": "m"},
    ]
    s = summarize(rows, "run-w", wall_s=6.0)
    assert (s["gate_requested"], s["gate"]) == (True, 1)


# ── Publicación de conocimiento (2026-11-17) ───────────────────────────────
# Medido: 42 ADRs ingeridos y 0 nodos del harness en el CKG de walker. El
# runner solo llamaba trace_push/capture/submit; `documenting_knowledge` no
# aparecía en ningún lado. Además `--walker-project` era manual: sin flag no
# se publicaba nada, sin aviso.

_SUMMARY = """## Goal
Hacer que el harness publique conocimiento.

## Constraints & Preferences
- no romper la medición

## Progress
### Done
- algo hecho

## Key Decisions
1. Ids determinísticos para que re-correr actualice.
2. Cortar la publicación si walker no responde,
   sin abortar la corrida.

## Next Steps
1. Verificar el CKG del proyecto.
2. Cerrar el sprint.

## Critical Context
- sin bloqueos
"""


def test_summary_knowledge_nodes_ids_deterministicos(tmp_path):
    path = tmp_path / "summary-demo-20261117.md"
    path.write_text(_SUMMARY)

    nodes = summary_knowledge_nodes(path)
    ids = [n["id"] for n in nodes]

    assert ids[0] == "session-demo-20261117"
    assert "session-demo-20261117-decision-01" in ids
    assert "session-demo-20261117-decision-02" in ids
    assert ids[-1] == "session-demo-20261117-next"
    assert [n["id"] for n in summary_knowledge_nodes(path)] == ids, (
        "el mismo resumen debe producir los mismos ids (upsert, no duplicado)"
    )


def test_summary_knowledge_nodes_tipos_y_continuacion(tmp_path):
    path = tmp_path / "summary-demo-20261117.md"
    path.write_text(_SUMMARY)

    nodes = {n["id"]: n for n in summary_knowledge_nodes(path)}
    decision = nodes["session-demo-20261117-decision-02"]

    assert nodes["session-demo-20261117"]["type"] == "implementation_state"
    assert decision["type"] == "design-decision"
    assert "sin abortar la corrida" in decision["label"], (
        "la línea de continuación del item numerado debe unirse"
    )
    assert "2)" in nodes["session-demo-20261117-next"]["description"]


def test_summary_sin_goal_no_inventa_nodos(tmp_path):
    path = tmp_path / "summary-vacio-20261117.md"
    path.write_text("## Goal\n\n## Key Decisions\n")
    assert summary_knowledge_nodes(path) == []


def test_eval_knowledge_nodes_un_nodo_por_fila_mas_la_suite():
    rows = [
        {"task": "incident-1", "k": 1, "pass": True, "walker_gate": None,
         "base": "abcdef123", "model": "m/x", "duration_s": 12.5,
         "tokens_in": 100, "tokens_out": 10, "test_rc": 0},
        {"task": "draw-2", "k": 1, "pass": False, "walker_gate": 0,
         "base": "abcdef123", "model": "m/x", "duration_s": 30.0,
         "tokens_in": 200, "tokens_out": 20, "test_rc": 1},
    ]
    nodes = eval_knowledge_nodes(rows, "20261117T101010")

    assert [n["id"] for n in nodes] == [
        "eval-incident-1-k1", "eval-draw-2-k1", "eval-suite-latest",
    ]
    assert nodes[0]["type"] == "verification"
    assert "result:pass" in nodes[0]["tags"]
    assert "result:fail" in nodes[1]["tags"]
    assert "1/2 pass" in nodes[-1]["label"]


def test_eval_knowledge_nodes_sin_filas_no_publica_nada():
    assert eval_knowledge_nodes([], "20261117T101010") == []


def test_detect_project_sin_registro_devuelve_none(tmp_path):
    """Sin proyecto registrado debe devolver None (no adivinar)."""
    assert detect_project(tmp_path) is None


def test_test_command_django_por_defecto():
    task = {"test": "tests.incident"}
    cmd = build_test_command(task, Path("/repo"), ".venv/bin/python", keepdb=True)

    assert cmd == ["/repo/.venv/bin/python", "manage.py", "test", "tests.incident", "--keepdb"]
    assert "--keepdb" not in build_test_command(
        task, Path("/repo"), ".venv/bin/python", keepdb=False
    )


def test_test_command_explicito_para_no_django():
    task = {"test_cmd": ["node", "--test", "{python}"]}
    cmd = build_test_command(task, Path("/repo"), ".venv/bin/python")

    assert cmd == ["node", "--test", "/repo/.venv/bin/python"], "sustituye {python}"
    assert "--keepdb" not in cmd
