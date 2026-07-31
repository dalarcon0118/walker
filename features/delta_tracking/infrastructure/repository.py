"""infrastructure/repository.py — Persistencia del TokenIndex DeltaTracking.

Usa data/delta.json.gz como storage, mismo formato que CPG y CKG.
"""

from __future__ import annotations
import gzip
import json
import os

from features.delta_tracking.domain.models import TokenIndex, IndexEntry


def save_index(index: TokenIndex, path: str) -> None:
    """Persiste el TokenIndex a data/delta.json.gz."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = index.to_dict()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if path.endswith(".gz"):
        open(path, "wb").write(gzip.compress(payload.encode()))
    else:
        open(path, "w").write(payload)


def load_index(path: str) -> TokenIndex:
    """Carga el TokenIndex desde data/delta.json.gz."""
    if not os.path.exists(path):
        return TokenIndex()
    try:
        if path.endswith(".gz"):
            raw = gzip.decompress(open(path, "rb").read()).decode()
        else:
            raw = open(path).read()
        data = json.loads(raw)
        index = TokenIndex()
        for token, entries_raw in data.items():
            for e in entries_raw:
                index.add(
                    token,
                    IndexEntry(
                        source=e.get("source", ""),
                        source_id=e.get("source_id", ""),
                        field=e.get("field", ""),
                        snippet=e.get("snippet", ""),
                        score=e.get("score", 1.0),
                    ),
                )
        return index
    except Exception:
        return TokenIndex()
