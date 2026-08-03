"""infrastructure/metrics_cache.py — Cache incremental de métricas qlty.

Optimización del review en build: evita re-ejecutar qlty (metrics, functions,
smells, lint) sobre archivos que NO cambiaron desde el último análisis.

Almacena por archivo (key = path absoluto) un snapshot {mtime, size} junto a:
  - metrics: dict de run_metrics para ese archivo
  - functions: list de run_function_metrics para ese archivo
  - smells: issues SARIF de ese archivo
  - lint: issues SARIF de ese archivo

Uso típico:
    cache = IncrementalMetricsCache(project_root, data_dir)
    fresh, stale = cache.get_stale_files(expanded_paths)
    # correr qlty solo sobre stale (batch)
    cache.update(stale_results)
    metrics, funcs, smells, lint = cache.aggregate(all_paths)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_CACHE_VERSION = 1


class IncrementalMetricsCache:
    """Cache incremental de métricas qlty por archivo + mtime/size."""

    def __init__(self, project_root: str, data_dir: str | None = None):
        self.project_root = os.path.abspath(project_root)
        self._cache_path = self._resolve_cache_path(data_dir)
        self._data: dict[str, Any] = self._load()
        self._hits = 0

    def _resolve_cache_path(self, data_dir: str | None) -> str:
        base = data_dir or os.path.join(self.project_root, "data")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "qlty_metrics_cache.json")

    def _load(self) -> dict:
        try:
            with open(self._cache_path) as f:
                data = json.load(f)
            if data.get("version") == _CACHE_VERSION:
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return {"version": _CACHE_VERSION, "files": {}}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._cache_path), exist_ok=True)
            tmp = self._cache_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._data, f, ensure_ascii=False)
            os.replace(tmp, self._cache_path)
        except OSError:
            pass

    # ── File discovery ────────────────────────────────────────────

    @staticmethod
    def _expand_paths(paths: list[str]) -> list[str]:
        """Expande directorios a archivos de código individuales."""
        code_exts = (
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".rs",
            ".go",
            ".java",
            ".rb",
            ".kt",
            ".swift",
            ".mjs",
            ".cjs",
        )
        files: list[str] = []
        for p in paths:
            if os.path.isfile(p):
                files.append(p)
            elif os.path.isdir(p):
                for root, dirs, names in os.walk(p):
                    dirs[:] = [
                        d
                        for d in dirs
                        if not d.startswith(".")
                        and d != "__pycache__"
                        and d != "node_modules"
                        and d != "venv"
                        and d != ".venv"
                        and d != "dist"
                        and d != "build"
                    ]
                    for n in names:
                        if n.endswith(code_exts):
                            files.append(os.path.join(root, n))
        return files

    def _file_key(self, filepath: str) -> str:
        try:
            return os.path.realpath(filepath)
        except (OSError, ValueError):
            return os.path.abspath(filepath)

    def _snapshot(self, filepath: str) -> dict | None:
        try:
            st = os.stat(filepath)
            return {"mtime": st.st_mtime, "size": st.st_size}
        except OSError:
            return None

    def _is_fresh(self, filepath: str, entry: dict) -> bool:
        snap = self._snapshot(filepath)
        if snap is None:
            return False
        stored = entry.get("_file")
        if stored is None:
            return False
        return (
            stored.get("mtime") == snap["mtime"] and stored.get("size") == snap["size"]
        )

    # ── Partition ─────────────────────────────────────────────────

    def get_stale_files(self, paths: list[str]) -> tuple[list[str], list[str]]:
        """Particiona paths en (fresh, stale).

        fresh: archivos con entry válida en cache (no re-ejecutar qlty).
        stale: archivos nuevos o modificados (correr qlty).
        """
        files = self._expand_paths(paths)
        fresh: list[str] = []
        stale: list[str] = []
        for fp in files:
            key = self._file_key(fp)
            entry = self._data["files"].get(key)
            if entry and self._is_fresh(fp, entry):
                fresh.append(fp)
            else:
                stale.append(fp)
        self._hits = len(fresh)
        return fresh, stale

    # ── Update (tras correr qlty en stale) ────────────────────────

    def update(
        self,
        filepath: str,
        metrics: dict | None = None,
        functions: list | None = None,
        smells: list | None = None,
        lint: list | None = None,
    ) -> None:
        """Guarda o actualiza las métricas cacheadas de un archivo."""
        key = self._file_key(filepath)
        snap = self._snapshot(filepath)
        if snap is None:
            return
        entry = self._data["files"].setdefault(key, {})
        entry["_file"] = snap
        if metrics is not None:
            entry["metrics"] = metrics
        if functions is not None:
            entry["functions"] = functions
        if smells is not None:
            entry["smells"] = smells
        if lint is not None:
            entry["lint"] = lint

    def save(self) -> None:
        self._save()

    def remove(self, filepath: str) -> None:
        key = self._file_key(filepath)
        self._data["files"].pop(key, None)

    # ── Aggregate ─────────────────────────────────────────────────

    def get(self, filepath: str) -> dict | None:
        key = self._file_key(filepath)
        entry = self._data["files"].get(key)
        if entry and self._is_fresh(filepath, entry):
            self._hits += 1
            return entry
        return None

    def all_files(self) -> list[str]:
        return list(self._data["files"].keys())

    @property
    def stats(self) -> dict:
        return {
            "path": self._cache_path,
            "cached_files": len(self._data.get("files", {})),
            "hits": self._hits,
        }
