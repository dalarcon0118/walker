"""F0 de AGENT-EFFICIENCY — telemetría por tool.

Contrato:
- toda llamada MCP deja una línea JSONL (tool, latencia, tokens, outcome);
- `outcome` distingue `hit | empty | error | invalid`;
- el resumen publica n, p50/p95, tasas y tokens (sin n y percentil no se
  reporta nada);
- la instrumentación es idempotente y no altera el resultado de la tool.
"""

from __future__ import annotations

import json

import anyio
import pytest

from fast_mcp.shared_kernel.telemetry import (
    classify_outcome,
    install_tool_metrics,
    metrics_path,
    read_records,
    record_call,
    summarize,
)


@pytest.fixture()
def metrics_file(tmp_path, monkeypatch):
    target = tmp_path / "tool_calls.jsonl"
    monkeypatch.setenv("WALKER_TOOL_METRICS_PATH", str(target))
    return target


def test_outcome_vacio_no_es_hit():
    assert classify_outcome(result='{"results": []}') == "empty"
    assert classify_outcome(result='{"ckg": {"results": [1, 2]}}') == "hit"
    assert classify_outcome(result="") == "empty"
    assert classify_outcome(result="x", error=ValueError("boom")) == "error"
    assert classify_outcome(result=None, error=ValueError("Unknown tool: x")) == "invalid"


def test_record_escribe_jsonl(metrics_file):
    record = record_call("agent_context", 2195, result='{"results": []}', project_id="walker")
    assert record is not None
    assert record.outcome == "empty"
    lines = metrics_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["tool"] == "agent_context"
    assert payload["latency_ms"] == 2195
    assert payload["project_id"] == "walker"
    assert payload["out_tokens"] == payload["out_chars"] // 4


def test_record_correlaciona_sesion_y_tarea(metrics_file):
    """Sin correlación no hay adopción por tarea, sólo agregados por tool."""
    record_call(
        "brief", 800, result='{"decisions": []}', project_id="walker",
        session_id="ses_1", task_id="t-1",
    )
    payload = json.loads(metrics_file.read_text(encoding="utf-8").splitlines()[0])
    assert payload["session_id"] == "ses_1"
    assert payload["task_id"] == "t-1"


def test_summary_publica_p50_p95_y_tasas(metrics_file):
    for latency, result in [(100, '{"a": 1}'), (200, '{"results": []}'), (3000, '{"a": 2}'), (400, "")]:
        record_call("workflow_state", latency, result=result)
    record_call("workflow_state", 50, error=ValueError("boom"))

    summary = summarize(read_records(metrics_path()))
    row = summary["tools"]["workflow_state"]
    assert row["n"] == 5
    assert row["p50_ms"] == 200
    assert row["p95_ms"] == 3000
    assert row["max_ms"] == 3000
    assert row["empty_rate"] == pytest.approx(0.4)
    assert row["error_rate"] == pytest.approx(0.2)


def test_install_es_idempotente_y_no_altera_resultado(metrics_file):
    class _Manager:
        async def call_tool(self, name, arguments=None, context=None, convert_result=False):
            if name == "boom":
                raise ValueError("boom")
            return "resultado suficientemente largo como para no ser vacío"

    class _Server:
        def __init__(self) -> None:
            self._tool_manager = _Manager()

    server = _Server()
    assert install_tool_metrics(server) is True
    assert install_tool_metrics(server) is False

    async def scenario():
        ok = await server._tool_manager.call_tool(
            "status", {"project_id": "walker", "session_id": "ses_9", "task_id": "t-9"},
        )
        with pytest.raises(ValueError):
            await server._tool_manager.call_tool("boom", {"project_id": "walker"})
        return ok

    result = anyio.run(scenario)
    assert result.startswith("resultado")

    records = read_records(metrics_path())
    assert [r["tool"] for r in records] == ["status", "boom"]
    assert records[0]["outcome"] == "hit"
    assert records[0]["project_id"] == "walker"
    assert records[0]["session_id"] == "ses_9", "la correlación viaja desde los args"
    assert records[0]["task_id"] == "t-9"
    assert records[1]["outcome"] == "error"


def test_read_records_ignora_lineas_corruptas(metrics_file):
    metrics_file.write_text('{"tool": "a"}\nno-json\n\n{"tool": "b"}\n', encoding="utf-8")
    records = read_records(metrics_file)
    assert [r["tool"] for r in records] == ["a", "b"]
