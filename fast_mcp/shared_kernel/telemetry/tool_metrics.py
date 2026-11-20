"""Telemetría por llamada de tool (F0 de AGENT-EFFICIENCY).

Qué mide y por qué:

- `latency_ms`: el agente decide por costo; sin p50/p95 no hay decisión.
- `out_tokens`: lo que la respuesta ocupa en contexto (chars/4 es la
  aproximación estándar; no se invoca un tokenizer en el camino caliente).
- `outcome`: `hit | empty | error | invalid`. `empty` es el caso que hoy mata la
  adopción: `agent_context(text=...)` tarda 2–3 s y devuelve `ckg.results: []`.
  Si no se mide, no se ve.
- `project_id`: se toma de los argumentos cuando la tool lo declara.

Formato: JSONL append-only (una línea por llamada). Nunca lanza: la telemetría
es observabilidad, no puede tumbar el server; un fallo de escritura queda en el
log y en `record_error`.

Ruta: `WALKER_TOOL_METRICS_PATH` > `<WALKER_ROOT>/data/metrics/tool_calls.jsonl`
> raíz del repo (derivada del archivo).
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

OUTCOMES = ("hit", "empty", "error", "invalid")

#: Firmas que delatan "no hay resultado" aun con HTTP 200.
_EMPTY_MARKERS = (
    '"results": []',
    '"entries": []',
    '"nodes": []',
    '"total": 0',
    '"items": []',
    '"matches": []',
)

#: Umbral de salida irrelevante: por debajo de esto no hay nada que leer.
#: Se deja bajo a propósito: `{"ok": true}` es corto pero es una respuesta; el
#: vacío real se detecta por marcadores (`"results": []`) o por texto en blanco.
_MIN_MEANINGFUL_CHARS = 2


@dataclass(frozen=True)
class CallRecord:
    """Una llamada a una tool, tal como la paga el agente."""

    tool: str
    latency_ms: int
    out_chars: int
    out_tokens: int
    outcome: str
    project_id: str = ""
    error: str = ""
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def metrics_path() -> Path:
    """Ruta del JSONL de métricas (override por entorno para tests)."""
    env = (os.environ.get("WALKER_TOOL_METRICS_PATH") or "").strip()
    if env:
        return Path(env)
    root = (os.environ.get("WALKER_ROOT") or "").strip()
    base = Path(root) if root else Path(__file__).resolve().parents[3]
    return base / "data" / "metrics" / "tool_calls.jsonl"


def _as_text(result: Any) -> str:
    """Serializa el resultado de una tool sin asumir su forma."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, (list, tuple)):
        parts: list[str] = []
        for item in result:
            text = getattr(item, "text", None)
            parts.append(text if isinstance(text, str) else str(item))
        return "\n".join(parts)
    text = getattr(result, "text", None)
    if isinstance(text, str):
        return text
    try:
        return json.dumps(result, default=str, ensure_ascii=False)
    except (TypeError, ValueError):  # pragma: no cover - defensivo
        return str(result)


def _is_invalid(error: BaseException | None) -> bool:
    if error is None:
        return False
    name = type(error).__name__
    if name in {"ValidationError", "ToolError"}:
        return True
    message = str(error).lower()
    return "unknown tool" in message or "validation" in message


def classify_outcome(result: Any = None, error: BaseException | None = None) -> str:
    """Clasifica una llamada en `hit | empty | error | invalid`."""
    if error is not None:
        return "invalid" if _is_invalid(error) else "error"
    text = _as_text(result)
    if len(text.strip()) < _MIN_MEANINGFUL_CHARS:
        return "empty"
    if any(marker in text for marker in _EMPTY_MARKERS):
        return "empty"
    return "hit"


def _project_id_from(arguments: dict[str, Any] | None) -> str:
    if not arguments:
        return ""
    for key in ("project_id", "_project_id", "project"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def record_call(
    tool: str,
    latency_ms: int,
    result: Any = None,
    error: BaseException | None = None,
    project_id: str = "",
) -> CallRecord | None:
    """Persiste una llamada. No lanza nunca (ver docstring del módulo)."""
    text = _as_text(result)
    record = CallRecord(
        tool=tool,
        latency_ms=max(0, int(latency_ms)),
        out_chars=len(text),
        out_tokens=len(text) // 4,
        outcome=classify_outcome(result, error),
        project_id=project_id,
        error=(str(error)[:200] if error is not None else ""),
    )
    path = metrics_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
    except OSError as exc:  # pragma: no cover - disco lleno/permisos
        logger.warning("tool_metrics: no se pudo escribir %s (%s)", path, exc)
        return None
    return record


def record_exception(tool: str, latency_ms: int, error: BaseException, project_id: str = "") -> None:
    """Atajo para el camino de error (misma ruta, outcome derivado)."""
    record_call(tool=tool, latency_ms=latency_ms, error=error, project_id=project_id)


def install_tool_metrics(server: Any) -> bool:
    """Envuelve `ToolManager.call_tool` para medir toda llamada MCP.

    Idempotente: un segundo llamado no re-envuelve (evita doble conteo cuando
    `build_server()` se invoca varias veces en el mismo proceso, p. ej. tests).

    Returns:
        True si aplicó la instrumentación; False si ya estaba.
    """
    manager = getattr(server, "_tool_manager", None)
    if manager is None:  # pragma: no cover - contrato de FastMCP
        return False
    current = getattr(manager, "call_tool", None)
    if current is None or getattr(current, "_walker_metrics", False):
        return False

    original = current

    async def call_tool(name, arguments=None, context=None, convert_result=False):  # type: ignore[no-untyped-def]
        started = time.perf_counter()
        try:
            result = await original(
                name,
                arguments or {},
                context=context,
                convert_result=convert_result,
            )
        except BaseException as error:  # noqa: BLE001 - se re-lanza intacto
            record_exception(
                tool=name,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=error,
                project_id=_project_id_from(arguments),
            )
            raise
        record_call(
            tool=name,
            latency_ms=int((time.perf_counter() - started) * 1000),
            result=result,
            project_id=_project_id_from(arguments),
        )
        return result

    setattr(call_tool, "_walker_metrics", True)
    manager.call_tool = call_tool  # type: ignore[method-assign]
    return True


def _percentile(values: list[int], pct: float) -> int:
    """Percentil por rango más cercano (sin numpy)."""
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrega por tool: n, p50/p95, tasa de vacío/error y tokens."""
    by_tool: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_tool.setdefault(str(record.get("tool") or "?"), []).append(record)

    tools: dict[str, Any] = {}
    for tool, rows in sorted(by_tool.items()):
        latencies = [int(r.get("latency_ms") or 0) for r in rows]
        total = len(rows)
        empty = sum(1 for r in rows if r.get("outcome") == "empty")
        errors = sum(1 for r in rows if r.get("outcome") == "error")
        invalid = sum(1 for r in rows if r.get("outcome") == "invalid")
        tokens = sum(int(r.get("out_tokens") or 0) for r in rows)
        tools[tool] = {
            "n": total,
            "p50_ms": _percentile(latencies, 50),
            "p95_ms": _percentile(latencies, 95),
            "max_ms": max(latencies) if latencies else 0,
            "empty_rate": round(empty / total, 3) if total else 0.0,
            "error_rate": round(errors / total, 3) if total else 0.0,
            "invalid_rate": round(invalid / total, 3) if total else 0.0,
            "out_tokens_total": tokens,
            "out_tokens_avg": round(tokens / total, 1) if total else 0.0,
        }

    return {
        "calls": len(records),
        "tools": tools,
        "out_tokens_total": sum(v["out_tokens_total"] for v in tools.values()),
    }


def read_records(path: Path | None = None) -> list[dict[str, Any]]:
    """Lee el JSONL; ignora líneas corruptas (un log roto no borra el resto)."""
    target = path or metrics_path()
    if not target.exists():
        return []
    records: list[dict[str, Any]] = []
    with target.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records
