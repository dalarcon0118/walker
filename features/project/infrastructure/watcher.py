"""
infrastructure/watcher.py — File change watcher.

SRP: Monitorea code_paths del proyecto, acumula cambios y verifica
     fingerprint del CPG para determinar si es espejo del código.

Layer: Infrastructure (pure IO — filesystem polling, snapshotting).
Does NOT import application layer or CognitiveGraph.
Tracing is delegated via an optional callback.
"""

from __future__ import annotations

import fnmatch
import gzip
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

IGNORE_FILENAME = ".walkerignore"
CHANGE_FILE = "change_detected.json"
MAX_CHANGED_FILES = 500


class IgnoreRules:
    """Parses .walkerignore patterns (like .gitignore)."""

    def __init__(self, project_root: str):
        self._project_root = str(project_root)
        self._patterns: list[str] = []
        ignore_path = os.path.join(project_root, IGNORE_FILENAME)
        self._load(ignore_path)

    def _load(self, path: str) -> None:
        if not os.path.exists(path):
            self._patterns = [
                "node_modules/", "__pycache__/", ".git/", ".expo/",
                ".venv/", "venv/", "dist/", "build/", ".next/",
                "*.pyc", "*.gz", "*.png", "*.jpg", "*.DS_Store",
                "package-lock.json", "yarn.lock",
            ]
            return
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    self._patterns.append(line)

    def is_ignored(self, rel_path: str) -> bool:
        for pattern in self._patterns:
            if fnmatch.fnmatch(rel_path, pattern):
                return True
        return False

    def filter_dirs(self, dirs: list[str]) -> list[str]:
        return [d for d in dirs if not self.is_ignored(f"{d}/") and not d.startswith(".")]


class FileWatcher:
    """Monitorea cambios en archivos del proyecto.

    Acumula cambios incrementalmente. Compara fingerprint contra CPG.
    Tracing delegado via on_trace_event callable (Liskov/IoC).
    """

    def __init__(
        self,
        project_root: str,
        code_paths: list[str] | None = None,
        *,
        data_dir: str | None = None,
        cpg_path: str | None = None,
        on_trace_event: Callable[[str, dict], None] | None = None,
    ):
        self.project_root = Path(project_root)
        self._ignore = IgnoreRules(str(project_root))

        if data_dir:
            self.data_dir = Path(data_dir)
        else:
            # Default inteligente: si el repo tiene layout cognitivo
            # (<root>/.cognitive/projects/<pid>/data), usar esa ruta CANONICA.
            # El legacy <repo>/data es solo para proyectos sin .cognitive
            # (tests/proyectos simples); escribir estados de watcher en el
            # repo root de un proyecto cognitivo los cae DENTRO del scope
            # escaneado -> auto-invalidacion en cada scan.
            self.data_dir = self._default_data_dir(project_root)

        self.change_path = self.data_dir / CHANGE_FILE
        self.snapshot_path = self.data_dir / ".file_snapshot.json"
        self._cpg_path_override = cpg_path

        if code_paths:
            self.watch_dirs = [self.project_root / p for p in code_paths]
        else:
            self.watch_dirs = [self.project_root]

        # Inyección: tracing handler (no-op por defecto, CognitiveGraph en producción)
        self._on_trace = on_trace_event or (lambda _event_type, _data: None)

    @staticmethod
    def _default_data_dir(project_root: str) -> Path:
        """Resuelve el data_dir por defecto del watcher.

        Prioridad:
        1. <project_root>/.cognitive/projects/<pid>/data — layout cognitivo
           CANONICO (el estado del watcher queda FUERA del árbol escaneado).
        2. <project_root>/data — legacy para proyectos sin .cognitive
           (tests, proyectos simples sin preparar).

        Si hay mas de un proyecto cognitivo bajo el root, usa el primero
        (mismo criterio que _find_cognitive_manifest en path_resolver).
        """
        import glob

        matches = sorted(
            glob.glob(
                os.path.join(project_root, ".cognitive", "projects", "*", "data")
            )
        )
        if matches:
            return Path(matches[0])
        return Path(project_root) / "data"

    # ── Public API ──

    def scan(self) -> dict[str, Any]:
        """Scan files, accumulate changes, verify against CPG fingerprint."""
        previous = self._load_snapshot()
        current = self._compute_snapshot()

        # If the previous snapshot used SHA256 content hashes and the new
        # snapshot uses lightweight size+mtime fingerprints, discard the
        # previous snapshot. Comparing across formats would report every
        # file as changed (false-positive storm).
        if previous and current:
            prev_sample = next(iter(previous.values()))
            curr_sample = next(iter(current.values()))
            if len(prev_sample) == 64 and ":" in curr_sample:
                previous = {}

        self._save_snapshot(current)

        is_baseline = not previous
        new_changes = self._diff(previous, current) if not is_baseline else []

        prev_status = self.get_status()
        accumulated = set(prev_status.get("files_changed", []))
        accumulated.update(new_changes)
        accumulated_list = sorted(accumulated)

        current_fingerprint = self.compute_fingerprint(current)
        cpg_fingerprint = self._read_cpg_fingerprint()
        cpg_is_mirror = bool(cpg_fingerprint) and current_fingerprint == cpg_fingerprint

        if cpg_is_mirror:
            accumulated_list = []
            cpg_status = "up_to_date"
        else:
            cpg_status = "stale" if accumulated_list else prev_status.get("cpg_status", "up_to_date")

        change_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "files_changed": accumulated_list[:MAX_CHANGED_FILES],
            "total_changes": len(accumulated_list),
            "new_changes": new_changes[:MAX_CHANGED_FILES],
            "fingerprint": current_fingerprint,
            "ckg_status": prev_status.get("ckg_status", "up_to_date"),
            "cpg_status": cpg_status,
        }

        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.change_path, "w") as f:
            json.dump(change_data, f, indent=2)

        if new_changes:
            self._on_trace("file_impact", {
                "files": new_changes[:MAX_CHANGED_FILES],
                "cpg_status": cpg_status,
                "total_files": len(new_changes),
            })

        return change_data

    def get_status(self) -> dict[str, Any]:
        """Read change_detected.json without re-scanning."""
        if self.change_path.exists():
            try:
                with open(self.change_path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "timestamp": "", "files_changed": [],
            "total_changes": 0, "new_changes": [],
            "fingerprint": "",
            "ckg_status": "up_to_date", "cpg_status": "up_to_date",
        }

    def mark_indexed(self, index_type: str = "cpg") -> None:
        """Mark an index as up_to_date after rebuild."""
        status = self.get_status()
        if index_type == "cpg":
            cpg_fp = self._read_cpg_fingerprint()
            current_fp = status.get("fingerprint", "")
            if cpg_fp and cpg_fp == current_fp:
                status["files_changed"] = []
                status["total_changes"] = 0
                status["cpg_status"] = "up_to_date"
            else:
                status["cpg_status"] = "stale"
        elif index_type == "ckg":
            status["ckg_status"] = "up_to_date"
        else:
            status["cpg_status"] = "up_to_date"

        with open(self.change_path, "w") as f:
            json.dump(status, f, indent=2)

        self._on_trace("workflow_state", {
            "index": index_type,
            "status": status.get(f"{index_type}_status", "up_to_date"),
        })

    # ── Fingerprint ──

    def compute_fingerprint(self, snapshot: dict[str, str] | None = None) -> str:
        """SHA256 fingerprint of source code.

        Aggregates lightweight per-file fingerprints (size + mtime) into
        a project-level hash. Changes when any tracked file is modified.
        """
        hasher = hashlib.sha256()
        items = sorted(snapshot.items()) if snapshot is not None else sorted(self._compute_snapshot().items())
        for rel, file_hash in items:
            hasher.update(rel.encode("utf-8"))
            hasher.update(b"\x00")
            hasher.update(file_hash.encode("utf-8"))
            hasher.update(b"\x00")
        return hasher.hexdigest()

    def _read_cpg_fingerprint(self) -> str:
        """Read source_fingerprint from CPG, if it exists."""
        for fname in ("cpg.json.gz", "cpg.json"):
            cpg_path = self._cpg_path_override or self.data_dir / fname
            cpg_path = Path(cpg_path) if not cpg_path else cpg_path
            if isinstance(cpg_path, Path) and cpg_path.exists():
                try:
                    if str(cpg_path).endswith(".gz"):
                        with gzip.open(cpg_path) as f:
                            data = json.load(f)
                    else:
                        with open(cpg_path) as f:
                            data = json.load(f)
                    return data.get("source_fingerprint", "") or ""
                except Exception:
                    pass
        return ""

    # ── Snapshot ──

    def _compute_snapshot(self) -> dict[str, str]:
        """Walk watch dirs, compute lightweight fingerprint per file.

        Uses size + mtime instead of content SHA256 to avoid reading every
        file. This makes incremental scans fast even for large codebases.
        """
        snapshot: dict[str, str] = {}
        for watch_dir in self.watch_dirs:
            if not watch_dir.is_dir():
                continue
            for root, dirs, files in os.walk(watch_dir):
                dirs[:] = self._ignore.filter_dirs(dirs)
                for fname in files:
                    fpath = os.path.join(root, fname)
                    rel = os.path.relpath(fpath, self.project_root)
                    if self._ignore.is_ignored(rel):
                        continue
                    try:
                        stat = os.stat(fpath)
                        snapshot[rel] = f"{stat.st_size}:{stat.st_mtime_ns}"
                    except (OSError, PermissionError):
                        continue
        return snapshot

    def _load_snapshot(self) -> dict[str, str]:
        if self.snapshot_path.exists():
            try:
                with open(self.snapshot_path) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_snapshot(self, snapshot: dict[str, str]) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.snapshot_path, "w") as f:
            json.dump(snapshot, f, indent=2)

    @staticmethod
    def _diff(previous: dict, current: dict) -> list[str]:
        changed = []
        all_keys = set(previous) | set(current)
        for key in all_keys:
            if previous.get(key) != current.get(key):
                changed.append(key)
        return changed
