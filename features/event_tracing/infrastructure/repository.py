"""infrastructure/repository.py — TraceRepository via CognitiveGraph.

Diseño ADR-WALKER-018:
  1. CognitiveGraph.search("trace", ...) es el storage canonical.
  2. load_traces() = canonical, con DEDUPE CENTRAL y orden determinista
     (contrato de unicidad del puerto).
  3. Cache en proceso por TTL: elimina el re-escaneo del hot path del prompt
     (ReflectionProvider.collect); se invalida al pushear.

Result pattern: los cores @safe nunca lanzan; los wrappers loggean
(convention-001: cero except silenciosos).
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

from features.event_tracing.application.ports.trace_repository import TraceRepository
from features.event_tracing.domain.models import TraceEvent

logger = logging.getLogger(__name__)

# Cache en proceso: (chain_dir -> (expira_en, eventos)).
_CACHE_TTL = 5.0
_CACHE: dict[str, tuple[float, list[TraceEvent]]] = {}


class CognitiveTraceRepository(TraceRepository):
    """Implementacion del puerto TraceRepository (indice trace).

    Facade delgada sobre las funciones de modulo (que resuelven project_root
    internamente). Permite inyeccion en consumidores (tests, services)
    manteniendo backward-compat con las funciones de modulo.
    """

    INDEX = "trace"

    def load_traces(self, chain_dir: str) -> list[TraceEvent]:
        return load_traces(chain_dir)

    def push_event(self, chain_dir: str, data: dict) -> dict:
        return push_event(chain_dir, data)


def load_traces(chain_dir: str) -> list[TraceEvent]:
    """Carga todos los traces disponibles para un sprint.

    Primary: CognitiveGraph (indice trace).
    Unicidad y orden: garantizados aqui (contrato del puerto, ADR-018).

    Args:
        chain_dir: Ruta al directorio del sprint

    Returns:
        Lista de TraceEvent (deduped, ordenada por timestamp).
    """
    if not chain_dir:
        return []

    cached = _CACHE.get(chain_dir)
    if cached and cached[0] > time.time():
        return cached[1]

    events: list[TraceEvent] = _load_canonical(chain_dir)
    events = _dedupe(events)
    events.sort(key=lambda ev: (ev.timestamp or "", ev.event_type or ""))
    _CACHE[chain_dir] = (time.time() + _CACHE_TTL, events)
    return events


def _load_canonical(chain_dir: str) -> list[TraceEvent]:
    """Lee del indice trace de CognitiveGraph (storage canonical)."""
    result = _search_cognitive_graph_core(chain_dir)
    if is_successful(result):
        return result.value_or([])
    error = result.failure()
    logger.warning(
        "CognitiveGraph trace no disponible (%s: %s); sin eventos canonicos",
        type(error).__name__, error,
    )
    return []


def push_event(chain_dir: str, data: dict) -> dict:
    """Almacena un evento en el indice trace (canonical).

    ADR-018: la feature es duena de su tool MCP — el handler de mcp.py delega
    aqui (no pasa por el task registry del engine). Invalida el cache del
    chain_dir para que la proxima lectura vea el evento.
    """
    _CACHE.pop(chain_dir, None)
    result = _push_event_core(chain_dir, data)
    if is_successful(result):
        return result.value_or({"ok": False, "error": "sin resultado"})
    error = result.failure()
    logger.error("push_event fallo (%s: %s)", type(error).__name__, error)
    return {"ok": False, "error": f"{type(error).__name__}: {error}"}


# ── Dedupe + determinismo (contrato del puerto) ─────────────────

def _dedupe(events: list[TraceEvent]) -> list[TraceEvent]:
    """Unicidad garantizada por firma (timestamp, event_type, tool, data).

    Conserva el primer evento de cada firma y su orden de llegada. Centralizar
    aqui elimina la compensacion que hacia reflection._dedupe_events (ADR-018).
    """
    seen: set[tuple] = set()
    result: list[TraceEvent] = []
    for ev in events:
        d = ev.to_dict()
        signature = (
            d.get("timestamp", ""),
            d.get("event_type", ""),
            d.get("tool", ""),
            json.dumps(d.get("data") or {}, sort_keys=True, default=str),
        )
        if signature in seen:
            continue
        seen.add(signature)
        result.append(ev)
    return result


# ── Resolucion de project_root ──────────────────────────────────

def _resolve_project_root(chain_dir: str) -> str:
    """Resuelve el project_root para el indice trace.

    Result pattern: el core @safe nunca lanza; ante fallo devuelve chain_dir
    y loggea en debug (convention-001).
    """
    result = _resolve_project_root_core(chain_dir)
    if is_successful(result):
        return result.value_or(chain_dir)
    error = result.failure()
    logger.debug(
        "project_root no resuelto desde %s (%s: %s); usando chain_dir",
        chain_dir, type(error).__name__, error,
    )
    return chain_dir


@safe
def _resolve_project_root_core(chain_dir: str) -> str:
    """Core puro de _resolve_project_root (puede lanzar)."""
    from libs.path_resolver import resolve_project_root

    resolved = resolve_project_root(chain_dir)
    return resolved or chain_dir


# ── Cores @safe (IO) ────────────────────────────────────────────

@safe
def _search_cognitive_graph_core(chain_dir: str) -> list[TraceEvent]:
    """Core puro: carga eventos via CognitiveGraph (puede lanzar).

    Reutiliza TraceEvent.from_dict (DRY, ADR-018): el mapeo de campos vive
    en un solo lugar. La raiz CG es project_root (consistente con el task
    trace_push del engine) con fallback a chain_dir.
    """
    from features.engine.bootstrap import get_module_service

    graph_factory = get_module_service("cognitive_graph")
    cg = graph_factory(_cg_root(chain_dir))
    result = cg.search("trace", {})
    events: list[TraceEvent] = []
    for h in result.get("hits", {}).get("hits", []):
        source = dict(h.get("_source", {}))
        source["id"] = h.get("_id", "")
        events.append(TraceEvent.from_dict(source))
    return events


@safe
def _push_event_core(chain_dir: str, data: dict) -> dict:
    """Core puro de push_event (puede lanzar).

    Respuesta: {"ok": bool, "data": {"event_id", "event_type"}}.
    """
    from features.engine.bootstrap import get_module_service

    ts = datetime.now(timezone.utc).isoformat()
    event_id = f"ev-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    event = {
        "event_type": data.get("event_type", "event"),
        "source": data.get("source", "walker_task"),
        "agent_id": data.get("agent_id") or os.environ.get("WALKER_AGENT_ID", ""),
        "session_id": data.get("session_id") or os.environ.get("WALKER_SESSION_ID", ""),
        "sprint_id": data.get("sprint_id", ""),
        "task_id": data.get("task_id", ""),
        "node": data.get("node", ""),
        "sub_state": data.get("sub_state", ""),
        "input": _coerce_json(data.get("input", {})),
        "output": _coerce_json(data.get("output", {})),
        "files": _coerce_list(data.get("files", [])),
        "timestamp": ts,
        "started_at": data.get("started_at", ts),
        "finished_at": data.get("finished_at", ts),
        "duration_ms": data.get("duration_ms", 0),
        "metadata": data.get("metadata", {}),
    }
    cg = get_module_service("cognitive_graph")(_cg_root(chain_dir))
    cg.store("trace", event_id, event)
    return {
        "ok": True,
        "data": {"event_id": event_id, "event_type": event["event_type"]},
    }


def _cg_root(chain_dir: str) -> str:
    """Raiz CG del indice trace: project_root si resuelve, si no chain_dir.

    Consistencia de datos (ADR-018): el task trace_push del engine escribe
    en la raiz del proyecto; la feature lee/escribe en la MISMA raiz para
    que los eventos sean visibles cruzados. En entornos sin proyecto
    (tests) el fallback es chain_dir.
    """
    root = _resolve_project_root(chain_dir)
    return root or chain_dir


def _coerce_json(value: Any) -> Any:
    """Convierte str JSON a objeto; si falla, deja el raw como {"raw": ...}."""
    result = _coerce_json_core(value)
    if is_successful(result):
        return result.value_or(value)
    return {"raw": value}


@safe
def _coerce_json_core(value: Any) -> Any:
    """Core puro de _coerce_json (puede lanzar JSONDecodeError)."""
    if isinstance(value, str):
        return json.loads(value)
    return value


def _coerce_list(value: Any) -> list:
    """Convierte str JSON de lista a list; si no es lista, lo envuelve."""
    if isinstance(value, list):
        return value
    result = _coerce_json_core(value)
    if is_successful(result) and isinstance(result.value_or(None), list):
        return result.value_or([])
    return [value]