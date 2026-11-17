"""test_units.py — tests del harness que no corren agentes.

    python3 -m pytest harness/test_units.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from runner import model_label, session_usage  # noqa: E402


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
