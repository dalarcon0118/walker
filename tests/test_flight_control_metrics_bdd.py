"""test_flight_control_metrics_bdd.py — el reporte que decide si el gate rinde.

`scripts/flight_control_metrics.py` agrega el trace en métricas comparables:
denials por gate, auto-declaraciones, ampliaciones de alcance, nudges de cierre,
verificaciones registradas por la tool y sugerencias seguidas/ignoradas.

El reporte excluye las sesiones del harness por defecto: sus denials son
intencionales y ensuciarían la lectura del uso real.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "flight_control_metrics.py"
_spec = importlib.util.spec_from_file_location("flight_control_metrics", _SCRIPT)
metrics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(metrics)


def _ev(kind: str, sid: str = "ses-1", **data) -> dict:
    return {"event_type": kind, "session_id": sid, "data": data}


def test_denials_por_gate_y_sesiones():
    events = [
        _ev("flight_control_denied", gate="bind"),
        _ev("flight_control_denied", gate="bind"),
        _ev("flight_control_denied", gate="scope", file="a.py"),
        _ev("flight_control_denied", "ses-2", gate="scope", file="b.py"),
    ]

    out = metrics.summarize(events)

    assert out["denials"]["by_gate"] == {"bind": 2, "scope": 2}
    assert out["denials"]["total"] == 4
    assert out["denials"]["sessions_with_denials"] == 2
    assert out["sessions"]["real"] == 2


def test_excluye_harness_por_defecto():
    events = [
        _ev("flight_control_denied", "harness-persist-1", gate="scope"),
        _ev("flight_control_denied", "evict-1", gate="bind"),
        _ev("flight_control_denied", "ses-real", gate="scope"),
    ]

    out = metrics.summarize(events)
    assert out["denials"]["total"] == 1
    assert out["sessions"]["harness_excluded"] == 2

    with_harness = metrics.summarize(events, include_harness=True)
    assert with_harness["denials"]["total"] == 3


def test_negociacion_y_retencion_visibles():
    events = [
        _ev("flight_control_autodeclared", task="declared-by-plan"),
        _ev("flight_control_scope_expanded", file="a.py"),
        _ev("flight_control_scope_expanded", file="a.py"),
        _ev("flight_control_scope_expanded", file="b.py"),
        _ev("close_blocked"),
    ]

    out = metrics.summarize(events)

    assert out["autodeclared"] == 1
    assert out["scope_expanded"]["events"] == 3
    assert out["scope_expanded"]["files"] == ["a.py", "b.py"]
    assert out["close_blocked"] == 1


def test_verificaciones_por_tool_separan_verde_y_rojo():
    events = [
        _ev("session_state", recorded_by="task_tool", verified=True),
        _ev("session_state", recorded_by="task_tool", verified=False),
        # Las que escribe el plugin no cuentan como verificación de la tool.
        _ev("session_state", recorded_by="agent_hook", verified=True),
    ]

    out = metrics.summarize(events)

    assert out["verifications"] == {"green": 1, "red": 1}


def test_sugerencias_seguidas_salen_de_state_injected():
    """P1.14: `ignored` sólo cuando el agente NO hizo nada. Una escritura,
    una verificación o un cambio de plan también son seguimiento."""
    events = [
        _ev("workflow_state_injected", followed=True, followedBy="edit"),
        _ev("workflow_state_injected", followed=True, followedBy="verify"),
        _ev("workflow_state_injected", followed=False),
        _ev("workflow_suggestion_ignored", reason="sin accion del agente"),
    ]

    out = metrics.summarize(events)

    sug = out["suggestions"]
    assert (sug["followed"], sug["ignored"]) == (2, 1)
    assert sug["by"] == {"edit": 1, "verify": 1}
    assert len(sug["ignored_detail"]) == 1


def test_evidencia_de_escritura_sin_hook():
    events = [
        _ev("session_state", recorded_by="task_tool", verified=True,
            touched_source="watcher", touched=["a.py", "b.py"]),
        _ev("session_state", recorded_by="task_tool", verified=True,
            touched_source="watcher", touched=["b.py", "c.py"]),
        _ev("session_state", recorded_by="agent_hook", wrote=True),
    ]

    out = metrics.summarize(events)

    assert out["write_evidence"] == {"events": 2, "files": 3}


def test_el_detalle_marca_una_ignorada_inconsistente():
    """Si el evento trae wrote/verified, el agente SÍ actuó: la marca de
    seguimiento falló. El reporte lo lista en vez de contarlo como desinterés."""
    events = [
        _ev("workflow_suggestion_ignored", position="IMPLEMENT.WORK",
            injections=2, wrote=True, verified=False),
        _ev("workflow_suggestion_ignored", position="IMPLEMENT.WORK",
            injections=1, wrote=False, verified=False,
            reason="sin accion del agente en la ejecucion"),
    ]

    out = metrics.summarize(events)

    detail = out["suggestions"]["ignored_detail"]
    assert out["suggestions"]["ignored"] == 2
    assert detail[0]["wrote"] is True and detail[0]["reason"] == ""
    assert detail[1]["reason"].startswith("sin accion")


def test_side_effects_sin_efecto_se_cuentan():
    """P1.18: el efecto que no se aplicó queda en el trace y el reporte lo ve,
    sin depender de que el agente haya mirado la respuesta del submit."""
    events = [
        _ev("side_effect_skipped", type="transform_spec", reason="sin sprint"),
        _ev("side_effect_skipped", type="transform_spec", reason="sin spec_id"),
        _ev("side_effect_failed", type="sprint_status", error="boom"),
    ]

    out = metrics.summarize(events)

    assert out["side_effects"] == {
        "skipped": 2,
        "failed": 1,
        "by": {"transform_spec": 2},
    }
