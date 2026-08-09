"""infrastructure/repository.py — TraceRepository via CognitiveGraph + legacy.

Diseño ADR-WALKER-018:
  1. CognitiveGraph.search("trace", ...) es el storage canonical.
  2. migrate_legacy_traces() consolida los formatos legacy (NDJSON + index)
     al indice trace — idempotente (ids deterministas por contenido).
  3. load_traces() = canonical + fallback de lectura legacy, con DEDUPE CENTRAL
     y orden determinista (contrato de unicidad del puerto).
  4. Cache en proceso por firma de fuentes (mtimes) + TTL: elimina el
     re-escaneo legacy del hot path del prompt (ReflectionProvider.collect).

Result pattern: los cores @safe nunca lanzan; los wrappers loggean
(convention-001: cero except silenciosos).
"""

from __future__ import annotations

import gzip
import hashlib
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

# Cache en proceso: (chain_dir -> (firma_fuentes, expira_en, eventos)).
# La firma son los mtimes de las fuentes legacy; si cambian, el cache se
# invalida. El TTL acota la staleness ante pushes recientes en el mismo run.
_CACHE_TTL = 5.0
_CACHE: dict[str, tuple[str, float, list[TraceEvent]]] = {}


class CognitiveTraceRepository(TraceRepository):
    """Implementacion del puerto TraceRepository (indice trace).

    Facade delgada sobre las funciones de modulo (que resuelven project_root
    internamente). Permite inyeccion en consumidores (tests, services)
    manteniendo backward-compat con las funciones de modulo.
    """

    INDEX = "trace"

    def load_traces(self, chain_dir: str) -> list[TraceEvent]:
        return load_traces(chain_dir)

    def migrate_legacy(self, chain_dir: str) -> int:
        return migrate_legacy_traces(chain_dir)

    def push_event(self, chain_dir: str, data: dict) -> dict:
        return push_event(chain_dir, data)


def load_traces(chain_dir: str) -> list[TraceEvent]:
    """Carga todos los traces disponibles para un sprint.

    Primary: CognitiveGraph (indice trace).
    Fallback: data/traces/*.json.gz y data/trace.json.gz (legacy).
    Unicidad y orden: garantizados aqui (contrato del puerto, ADR-018).

    Args:
        chain_dir: Ruta al directorio del sprint

    Returns:
        Lista de TraceEvent (deduped, ordenada por timestamp).
    """
    if not chain_dir:
        return []

    # Cache por firma de fuentes legacy (hot path del prompt).
    sig = _legacy_signature(chain_dir)
    cached = _CACHE.get(chain_dir)
    if cached and cached[1] > time.time() and cached[0] == sig:
        return cached[2]

    events: list[TraceEvent] = []
    events.extend(_load_canonical(chain_dir))
    events.extend(_load_legacy(chain_dir))

    events = _dedupe(events)
    events.sort(key=lambda ev: (ev.timestamp or "", ev.event_type or ""))
    _CACHE[chain_dir] = (sig, time.time() + _CACHE_TTL, events)
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


def _load_legacy(chain_dir: str) -> list[TraceEvent]:
    """Fallback de lectura: NDJSON (data/traces/) + index (trace.json.gz)."""
    project_root = _resolve_project_root(chain_dir)
    events: list[TraceEvent] = []
    for base in _ordered_bases(chain_dir, project_root):
        trace_dir = os.path.join(base, "data", "traces")
        if os.path.isdir(trace_dir):
            for fname in sorted(os.listdir(trace_dir)):
                if not fname.endswith(".gz"):
                    continue
                fpath = os.path.join(trace_dir, fname)
                result = _read_ndjson_core(fpath)
                if is_successful(result):
                    events.extend(result.value_or([]))
                else:
                    error = result.failure()
                    logger.warning(
                        "trace NDJSON %s ilegible (%s: %s); archivo omitido",
                        fpath, type(error).__name__, error,
                    )
        index_path = os.path.join(base, "data", "trace.json.gz")
        if os.path.isfile(index_path):
            result = _read_index_core(index_path)
            if is_successful(result):
                events.extend(result.value_or([]))
            else:
                error = result.failure()
                logger.warning(
                    "trace index %s ilegible (%s: %s); omitido",
                    index_path, type(error).__name__, error,
                )
    return events


def migrate_legacy_traces(chain_dir: str) -> int:
    """Migra formatos legacy (NDJSON + index) al indice trace.

    Idempotente: los doc ids son deterministas (hash del contenido), por lo
    que re-ejecutar sobreescribe los mismos docs sin duplicar.

    Args:
        chain_dir: Ruta al directorio del sprint

    Returns:
        Numero de eventos migrados (0 si no hay legacy o falla el store).
    """
    _CACHE.pop(chain_dir, None)
    legacy = _load_legacy(chain_dir)
    events = _dedupe(legacy)
    if not events:
        return 0
    result = _store_events_core(chain_dir, events)
    if is_successful(result):
        stored = result.value_or(0)
        logger.info("migrate_legacy_traces: %d eventos migrados a indice trace", stored)
        return stored
    error = result.failure()
    logger.error(
        "migrate_legacy_traces fallo (%s: %s); %d eventos no migrados",
        type(error).__name__, error, len(events),
    )
    return 0


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


def _ordered_bases(chain_dir: str, project_root: str) -> list[str]:
    """Bases de busqueda legacy en orden determinista (sin duplicados)."""
    bases: list[str] = []
    for b in (chain_dir, project_root):
        if b and b not in bases:
            bases.append(b)
    return bases


def _legacy_signature(chain_dir: str) -> str:
    """Firma (mtimes) de las fuentes legacy para invalidar el cache."""
    project_root = _resolve_project_root(chain_dir)
    parts: list[str] = []
    for base in _ordered_bases(chain_dir, project_root):
        trace_dir = os.path.join(base, "data", "traces")
        if os.path.isdir(trace_dir):
            for fname in sorted(os.listdir(trace_dir)):
                if not fname.endswith(".gz"):
                    continue
                fp = os.path.join(trace_dir, fname)
                try:
                    parts.append(f"{fname}:{os.path.getmtime(fp)}")
                except OSError:
                    parts.append(f"{fname}:?")
        index_path = os.path.join(base, "data", "trace.json.gz")
        if os.path.isfile(index_path):
            try:
                parts.append(f"index:{os.path.getmtime(index_path)}")
            except OSError:
                parts.append("index:?")
    return "|".join(parts)


# ── Resolucion de project_root ──────────────────────────────────

def _resolve_project_root(chain_dir: str) -> str:
    """Resuelve el project_root para buscar traces a nivel proyecto.

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
def _read_ndjson_core(fpath: str) -> list[TraceEvent]:
    """Core puro: lee un archivo NDJSON gz (puede lanzar)."""
    with gzip.open(fpath, "rt") as f:
        content = f.read()
    events: list[TraceEvent] = []
    for line in content.strip().split("\n"):
        line = line.strip()
        if line:
            ev = TraceEvent.from_ndjson(line)
            if ev:
                events.append(ev)
    return events


@safe
def _read_index_core(index_path: str) -> list[TraceEvent]:
    """Core puro: lee el index legacy trace.json.gz (puede lanzar)."""
    with gzip.open(index_path, "rt") as f:
        raw = json.load(f)
    docs = raw.get("docs", []) if isinstance(raw, dict) else raw
    events: list[TraceEvent] = []
    for doc in docs:
        if isinstance(doc, dict):
            events.append(TraceEvent.from_dict(doc))
    return events


@safe
def _store_events_core(chain_dir: str, events: list[TraceEvent]) -> int:
    """Core puro: persiste eventos en el indice trace (puede lanzar).

    Doc ids deterministas (hash del contenido) -> migracion idempotente.
    """
    from features.engine.bootstrap import get_module_service

    graph_factory = get_module_service("cognitive_graph")
    cg = graph_factory(_cg_root(chain_dir))
    docs: dict[str, dict] = {}
    for ev in events:
        doc_id = _event_doc_id(ev)
        docs[doc_id] = ev.to_dict()
    if docs:
        cg.store_many("trace", docs)
    return len(docs)


def _event_doc_id(ev: TraceEvent) -> str:
    """Id de documento determinista: id explicito o hash del contenido."""
    if ev.id:
        return ev.id
    payload = json.dumps(ev.to_dict(), sort_keys=True, default=str)
    return f"ev-{hashlib.md5(payload.encode()).hexdigest()[:16]}"


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
