"""test_agent_adoption_bdd.py — el reporte de adopción no inventa números.

AE-INV-08: sin ratio Walker/nativas y tokens publicados no hay "verde". Hoy el
harness no expone las llamadas nativas ni el intento (1º/2º) de verificación,
así que el reporte los declara `null` con el motivo — no los estima.
"""

from __future__ import annotations

import sqlite3

from scripts.agent_adoption import read_harness, summarize_adoption, summarize_tasks


def _call(tool: str = "brief", outcome: str = "hit", tokens: int = 20,
          ts: str = "", session_id: str = "", task_id: str = "") -> dict:
    return {
        "tool": tool, "latency_ms": 10, "out_chars": 100, "out_tokens": tokens,
        "outcome": outcome, "project_id": "p", "error": "",
        "ts": ts, "session_id": session_id, "task_id": task_id,
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
    assert "first_pass_tests" in summary["unavailable"]


def test_sin_verificaciones_el_rework_es_nulo():
    summary = summarize_adoption([_call()], [])

    assert summary["rework_rate"] is None, "no se divide por cero"


def test_adopcion_por_tarea_mira_si_el_brief_llego_primero():
    tasks = summarize_tasks([
        _call("brief", ts="2026-11-20T10:00:00", session_id="s1", task_id="t1"),
        _call("submit", ts="2026-11-20T10:00:05", session_id="s1", task_id="t1"),
        _call("search_memories", ts="2026-11-20T10:00:01", session_id="s2", task_id="t2"),
        _call("submit", ts="2026-11-20T10:00:02", session_id="s2", task_id="t2"),
        _call("brief", ts="2026-11-20T10:00:03", session_id="s2", task_id="t2"),
        _call("brief", ts="2026-11-20T10:00:04"),  # sin session_id: no se puede agrupar
    ])

    assert tasks["tasks_total"] == 2
    assert tasks["tasks_with_brief"] == 2
    assert tasks["tasks_brief_first"] == 1, "sólo t1 abrió con brief"
    assert tasks["tasks_brief_before_write"] == 1, "en t2 el brief llegó después del submit"


def test_sin_correlacion_la_adopcion_por_tarea_es_nd():
    summary = summarize_adoption([_call("brief")], [])
    assert summary["tasks"]["tasks_total"] == 0
    assert "tasks" in summary["unavailable"]


def test_read_harness_calcula_el_ratio(tmp_path):
    db = tmp_path / "harness.db"
    con = sqlite3.connect(db)
    con.execute("create table session (id text, directory text, time_created integer)")
    con.execute("create table part (session_id text, data text)")
    con.execute(
        "insert into session values ('s1', '/x/Active-projects/walker', ?)",
        (1_800_000_000_000,),
    )
    for tool in ("walker_brief", "read", "bash"):
        con.execute(
            "insert into part values (?, ?)",
            ("s1", '{"type":"tool","tool":"%s"}' % tool),
        )
    con.commit(); con.close()

    out = read_harness(
        db, project_path="Active-projects/walker", now_ms=1_800_000_100_000
    )

    assert out["walker_calls"] == 1
    assert out["native_calls"] == 2
    assert out["native_ratio"] == 0.5
    assert out["stale"] is False


def test_read_harness_no_publica_un_ratio_viejo(tmp_path):
    """Una DB congelada no representa la superficie actual: ratio N/D."""
    db = tmp_path / "harness.db"
    con = sqlite3.connect(db)
    con.execute("create table session (id text, directory text, time_created integer)")
    con.execute("create table part (session_id text, data text)")
    con.execute(
        "insert into session values ('s1', '/x/Active-projects/walker', ?)",
        (1_700_000_000_000,),
    )
    for tool in ("walker_brief", "read", "bash"):
        con.execute(
            "insert into part values (?, ?)",
            ("s1", '{"type":"tool","tool":"%s"}' % tool),
        )
    con.commit(); con.close()

    out = read_harness(
        db, project_path="Active-projects/walker", now_ms=1_800_000_000_000
    )
    summary = summarize_adoption([], [], out)

    assert out["walker_calls"] == 1, "el conteo se publica"
    assert out["native_ratio"] is None, "pero el ratio no se declara actual"
    assert out["measured_ratio"] == 0.5
    assert out["age_days"] > 2
    assert summary["native_ratio"] is None
    assert "native_ratio" in summary["unavailable"]
