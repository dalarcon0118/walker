"""Cache para métricas qlty (evita re-ejecutar en archivos no modificados).

Uso:
    cache = MetricsCache(chain_dir)
    metrics = cache.get_metrics(filepath)
    if metrics is None:
        metrics = run_metrics(filepath)
        cache.set_metrics(filepath, metrics)
"""

from __future__ import annotations

import json
from pathlib import Path


class MetricsCache:
    """Cache persistente de métricas qlty por archivo + mtime.

    Almacena results de run_metrics y run_metrics_functions.
    Invalida entradas cuando el archivo cambia (mtime o tamaño).
    """

    _CACHE_FILENAME = ".codereview_cache.json"

    def __init__(self, chain_dir: str | Path | None = None):
        self._cache_path = self._resolve_cache_path(chain_dir)
        self._data: dict = self._load()

    def _resolve_cache_path(self, chain_dir: str | Path | None) -> Path:
        """Determina ruta del archivo de cache."""
        if chain_dir:
            base = Path(chain_dir)
            if base.is_dir():
                return base / self._CACHE_FILENAME
            return base.parent / self._CACHE_FILENAME
        # Fallback: proyecto actual
        cwd = Path.cwd()
        for p in [cwd] + list(cwd.parents):
            if (p / ".cognitive").exists() or (p / ".git").exists():
                return p / ".cognitive" / self._CACHE_FILENAME
        return cwd / self._CACHE_FILENAME

    def _load(self) -> dict:
        try:
            with open(self._cache_path) as f:
                data = json.load(f)
            if data.get("version") == 1:
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return {"version": 1, "files": {}}

    def _save(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._cache_path, "w") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def _file_key(self, filepath: Path) -> str:
        """Key normalizada para el archivo."""
        try:
            return str(filepath.resolve())
        except (OSError, ValueError):
            return str(filepath)

    def _file_snapshot(self, filepath: Path) -> dict | None:
        """Obtiene mtime + size del archivo. None si no existe."""
        try:
            stat = filepath.stat()
            return {"mtime": stat.st_mtime, "size": stat.st_size}
        except OSError:
            return None

    def _is_fresh(self, filepath: Path, entry: dict) -> bool:
        """Verifica si la entrada en cache sigue siendo válida."""
        current = self._file_snapshot(filepath)
        if current is None:
            return False
        stored = entry.get("_file")
        if stored is None:
            return False
        return (
            stored.get("mtime") == current["mtime"]
            and stored.get("size") == current["size"]
        )

    def get_metrics(self, filepath: Path) -> dict | None:
        """Retorna métricas cacheadas si el archivo no ha cambiado."""
        key = self._file_key(filepath)
        entry = self._data["files"].get(key)
        if entry and self._is_fresh(filepath, entry):
            return entry.get("metrics")
        return None

    def set_metrics(self, filepath: Path, metrics: dict) -> None:
        """Guarda métricas en cache."""
        snap = self._file_snapshot(filepath)
        if snap is None:
            return
        key = self._file_key(filepath)
        self._data["files"].setdefault(key, {})["_file"] = snap
        self._data["files"][key]["metrics"] = metrics
        self._save()

    def get_functions(self, filepath: Path) -> list[dict] | None:
        """Retorna funciones cacheadas si el archivo no ha cambiado."""
        key = self._file_key(filepath)
        entry = self._data["files"].get(key)
        if entry and self._is_fresh(filepath, entry):
            return entry.get("functions")
        return None

    def set_functions(self, filepath: Path, functions: list[dict]) -> None:
        """Guarda funciones en cache."""
        snap = self._file_snapshot(filepath)
        if snap is None:
            return
        key = self._file_key(filepath)
        self._data["files"].setdefault(key, {})["_file"] = snap
        self._data["files"][key]["functions"] = functions
        self._save()

    def invalidate(self, filepath: Path) -> None:
        """Invalida cache para un archivo."""
        key = self._file_key(filepath)
        self._data["files"].pop(key, None)

    @property
    def cache_path(self) -> Path:
        return self._cache_path

    @property
    def hit_count(self) -> int:
        return getattr(self, "_hits", 0)

    @hit_count.setter
    def hit_count(self, v: int) -> None:
        self._hits = v

    @property
    def stats(self) -> dict:
        files = self._data.get("files", {})
        return {
            "path": str(self._cache_path),
            "cached_files": len(files),
            "hits": getattr(self, "_hits", 0),
        }
