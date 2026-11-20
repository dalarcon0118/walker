"""test_agent_adoption_bdd.py — el reporte de adopción no inventa números.

AE-INV-08: sin ratio Walker/nativas y tokens publicados no hay "verde". Hoy el
harness no expone las llamadas nativas ni el intento (1º/2º) de verificación,
así que el reporte los declara `null` con el motivo — no los estima.
"""

from __future__ import annotations

from scripts.agent_adoption import summarize_adoption


def _call(tool: str = "brief", outcome: str = "hit", tokens: int = 20) -> dict:
    return {
        "tool": tool, "latency_ms": 10, "out_chars": 100, "out_tokens": tokens,
        "outcome": outcome, "project_id": "p", "error": "",
    }


def test_agrega_tools_y_declara_lo_no_medible():
    summary = summarize_adoption(
        [_call(), _call("search_memories", outcome="empty")],
        [
            {"event_type": "verification", "data": {"session_id": "s1", "passed": True}},
            {"event_type": "verification", "data": {"session_id": "s1", "passed": False}},
            {"event_type": "session_metrics", "data": {"session_id": "s1"}},
        ],
    )

    assert summary["calls"] == 2
    assert summary["out_tokens_total"] == 40
    assert summary["tools"]["brief"]["n"] == 1
    assert summary["tools"]["search_memories"]["empty_rate"] == 1.0
    assert summary["sessions"] == 1
    assert summary["verifications"] == 2
    assert summary["rework_rate"] == 0.5
    assert summary["native_ratio"] is None
    assert summary["first_pass_tests"] == 0, "la sesión no está verificada"
    assert "native_ratio" in summary["unavailable"]


def test_sin_verificaciones_el_rework_es_nulo():
    summary = summarize_adoption([_call()], [])

    assert summary["rework_rate"] is None, "no se divide por cero"
