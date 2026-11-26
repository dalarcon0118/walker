"""Una sola fórmula de metacognición (ADR-WALKER-084).

Contrato:
- `brief` y `scripts/metacognition_score.py` usan `compute_score`/`compute_metacognition`.
- Las tool calls se atribuyen por `session_id`; si falta, por `task_id` (fallback
  explícito, no se descartan en silencio).
- Sin datos el score es `None`, nunca 0.
- `mirror_acted_on` sólo cuenta si el remedio aparece en las próximas 5 tools.
"""

from __future__ import annotations

from types import SimpleNamespace

from fast_mcp.features.event_tracing.application.services.metacognition import (
    compute_metacognition,
    compute_score,
    mirror_acted_on,
    tool_counts,
)
from fast_mcp.phases.phase2_memories import _INPUT_SCHEMAS as MEMORY_SCHEMAS
from fast_mcp.phases.phase3_tasks import _INPUT_SCHEMAS as TASK_SCHEMAS


def _record(tool: str, session_id: str = "", task_id: str = "", ts: str = "2026-11-26T00:00:00+00:00"):
    return {"tool": tool, "session_id": session_id, "task_id": task_id, "ts": ts}


def test_tool_counts_por_sesion_y_fallback_por_tarea():
    records = [
        _record("search_memories", session_id="ses_a"),
        _record("run_tests", task_id="SPEC-1"),
        _record("brief", session_id="ses_b"),
        _record("search_memories", session_id="ses_b"),
    ]

    counts = tool_counts(records, session_id="ses_a", task_id="SPEC-1")

    assert counts["memory"] == 1
    assert counts["verify"] == 1  # fallback: sin sesión, misma tarea
    assert counts["brief"] == 0
    assert counts["total"] == 2


def test_sin_datos_el_score_es_none_no_cero():
    result = compute_score({}, {"memory": 0, "strategy": 0, "verify": 0})

    assert result["score"] is None
    assert result["missing"]


def test_brief_y_script_comparten_la_formula():
    state = {
        "writes": 1,
        "verified": True,
        "denials": {"total": 0},
        "gaps": [],
        "brief_reads": 2,
        "brief_with_gaps": 1,
        "mirror_acted": 1,
        "close_blocked": 0,
        "orphan_specs": 0,
        "workflow_ignored": 0,
    }
    records = [_record("search_memories", session_id="ses_x")]
    counts = tool_counts(records, session_id="ses_x")

    expected = compute_score(
        state, counts, mirror_acted=1, brief_with_gaps=1
    )["score"]
    result = compute_metacognition(state, session_id="ses_x", records=records)

    assert result["score"] == expected
    assert result["knowledge"]["self"]["verified"] is True


def test_mirror_acted_cuenta_el_remedio_siguiente():
    event = SimpleNamespace(
        event_type="brief_injected",
        data={"gaps": ["denials"]},
        timestamp="2026-11-26T00:00:00+00:00",
    )
    records = [
        _record("session_bind", session_id="ses_x", ts="2026-11-26T00:00:01+00:00"),
        _record("brief", session_id="ses_x", ts="2026-11-26T00:00:02+00:00"),
    ]

    result = mirror_acted_on([event], records, "ses_x")

    assert result["brief_reads_with_gaps"] == 1
    assert result["acted"] == 1
    assert result["rate"] == 1.0


def test_las_tools_de_lectura_aceptan_session_id_para_la_telemetria():
    assert "session_id" in MEMORY_SCHEMAS["search_memories"]["properties"]
    assert "session_id" in MEMORY_SCHEMAS["agent_context"]["properties"]
    assert "session_id" in TASK_SCHEMAS["run_tests"]["properties"]
    assert "session_id" in TASK_SCHEMAS["verify_predicates"]["properties"]
