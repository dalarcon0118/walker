"""fast_mcp/features/code_analysis/application/projection_helpers.py

Shared helpers for CPG projections and on-demand scans used by
inspect_codebase actions (dataflow/controlflow) and other consumers.
All I/O is explicit; fail-fast on missing data with actionable remediation.
"""

from __future__ import annotations

from typing import Any

from fast_mcp.shared_kernel.projection_store import ProjectionStore


def load_projection(data_dir: str, name: str) -> dict:
    """Load a CPG projection (dfg.json / analysis_results.json) from data_dir.

    Fail-fast: if the projection does not exist, raise ValueError with
    remediation ('ejecute build_code_index').
    """
    store = ProjectionStore(data_dir)
    data = store.read(name)
    if not data:
        raise ValueError(
            f"Proyección '{name}' no encontrada en {data_dir}. "
            "Ejecuta build_code_index primero."
        )
    return data


def filter_entries(entries: list[dict], symbol: str, keys: list[str]) -> list[dict]:
    """Filter projection entries involving a symbol in any of the given keys."""
    symbol_lower = symbol.lower()
    return [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and any(
            symbol_lower in str(entry.get(key, "")).lower()
            for key in keys
        )
    ]


def filter_by_file(entries: list[dict], file_path: str) -> list[dict]:
    """Filter projection entries by file path (normalized, case-insensitive)."""
    file_norm = file_path.lower().replace("\\", "/")
    return [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and file_norm in entry.get("file", "").lower().replace("\\", "/")
    ]
