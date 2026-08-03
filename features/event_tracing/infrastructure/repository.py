"""infrastructure/repository.py — Carga traces via CognitiveGraph + fallback legacy.

Flujo:
  1. Primary: CognitiveGraph.search("trace", {"task_id": ...})
  2. Fallback: data/traces/*.json.gz (NDJSON legacy)
  3. Fallback: data/trace.json.gz (index format legacy)

El storage canonical es CognitiveGraph (indice trace). Los formatos
legacy se mantienen para lectura de datos existentes.
"""

from __future__ import annotations
import gzip
import json
import os
from features.event_tracing.domain.models import TraceEvent


def load_traces(chain_dir: str) -> list[TraceEvent]:
    """Carga todos los traces disponibles para un sprint.

    Primary: CognitiveGraph (indice trace).
    Fallback: data/traces/*.json.gz y data/trace.json.gz.

    Args:
        chain_dir: Ruta al directorio del sprint

    Returns:
        Lista de TraceEvent
    """
    events: list[TraceEvent] = []

    # ── Fuente 1: CognitiveGraph (storage canonical) ──────────────
    try:
        from libs.cognitive_graph import CognitiveGraph
        cg = CognitiveGraph(chain_dir)
        result = cg.search("trace", {})
        for h in result.get("hits", {}).get("hits", []):
            source = h.get("_source", {})
            ev = TraceEvent(
                id=h.get("_id", ""),
                event_type=source.get("event_type", ""),
                source=source.get("source", "walker"),
                agent_id=source.get("agent_id", ""),
                session_id=source.get("session_id", ""),
                sprint_id=source.get("parent_id", ""),
                task_id=source.get("task_id", ""),
                node=source.get("node", ""),
                sub_state=source.get("sub_state", ""),
                tool=source.get("tool", ""),
                input=source.get("input", {}),
                output=source.get("output", {}),
                files=source.get("files", []),
                timestamp=source.get("timestamp", ""),
                started_at=source.get("started_at", ""),
                finished_at=source.get("finished_at", ""),
                duration_ms=source.get("duration_ms", 0),
                metadata=source.get("metadata", {}),
                data=source.get("data", {}),
            )
            events.append(ev)
    except Exception:
        pass

    # ── Resolver project_root para buscar traces legacy a nivel proyecto ──
    project_root = chain_dir
    try:
        from libs.path_resolver import resolve_project_root
        resolved = resolve_project_root(chain_dir)
        if resolved:
            project_root = resolved
    except Exception:
        pass

    # ── Fuente 2: NDJSON en data/traces/ (legacy) ─────────────────
    # Busca en: <sprint>/data/traces/ Y <project>/data/traces/
    for base in {chain_dir, project_root}:
        trace_dir = os.path.join(base, "data", "traces")
        if not os.path.isdir(trace_dir):
            continue
        for fname in sorted(os.listdir(trace_dir)):
            if not fname.endswith(".gz"):
                continue
            fpath = os.path.join(trace_dir, fname)
            try:
                with gzip.open(fpath, "rt") as f:
                    content = f.read()
                for line in content.strip().split("\n"):
                    line = line.strip()
                    if line:
                        ev = TraceEvent.from_ndjson(line)
                        if ev:
                            events.append(ev)
            except Exception:
                continue

    # ── Fuente 3: trace.json.gz (index format legacy) ─────────────
    # Busca en: <sprint>/data/trace.json.gz Y <project>/data/trace.json.gz
    for base in {chain_dir, project_root}:
        index_path = os.path.join(base, "data", "trace.json.gz")
        if os.path.isfile(index_path):
            try:
                with gzip.open(index_path, "rt") as f:
                    raw = json.load(f)
                docs = raw.get("docs", []) if isinstance(raw, dict) else raw
                for doc in docs:
                    if isinstance(doc, dict):
                        events.append(TraceEvent.from_dict(doc))
            except Exception:
                pass

    return events
