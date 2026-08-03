"""
infrastructure/watcher.py — File change watcher.

SRP: Monitorea code_paths del proyecto, acumula cambios y verifica
     contra el fingerprint del CPG para determinar si el CPG es espejo del código.

COMPORTAMIENTO:
  - scan() acumula cambios incrementalmente (NUNCA resetea files_changed)
  - Compara fingerprint actual contra el source_fingerprint almacenado en CPG
  - Si fingerprint coincide → CPG es espejo del código → limpia acumulados
  - mark_indexed() verifica contra CPG real antes de limpiar
"""
from __future__ import annotations

import fnmatch
import gzip
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
            if pattern.endswith("/") and (rel_path.startswith(pattern) or rel_path == pattern.rstrip("/")):
                return True
        return False

    def filter_dirs(self, dirs: list[str]) -> list[str]:
        return [d for d in dirs if not self.is_ignored(f"{d}/") and not d.startswith(".")]


class FileWatcher:
    """Monitorea cambios en archivos del proyecto.

    Acumula cambios incrementalmente. Compara fingerprint contra CPG
    para determinar si el CPG es un espejo fiel del código fuente.

    El files_changed se acumula hasta que CPG se reconstruye Y su
    source_fingerprint coincide con el fingerprint actual del código.
    """

    def __init__(self, project_root: str, code_paths: list[str] | None = None):
        self.project_root = Path(project_root)
        self._ignore = IgnoreRules(str(project_root))
        self.data_dir = self._resolve_data_dir(project_root)
        self.change_path = self.data_dir / CHANGE_FILE
        self.snapshot_path = self.data_dir / ".file_snapshot.json"

        if code_paths:
            self.watch_dirs = [self.project_root / p for p in code_paths]
        else:
            self.watch_dirs = [self.project_root]

    # ── Public API ───────────────────────────────────────────────────

    def scan(self) -> dict[str, Any]:
        """Escanear archivos, acumular cambios, verificar contra CPG.

        Returns:
            dict con change_detected status actualizado.
        """
        previous = self._load_snapshot()
        current = self._compute_snapshot()
        self._save_snapshot(current)

        # 1. Detectar cambios nuevos desde último scan
        is_baseline = not previous
        new_changes = self._diff(previous, current) if not is_baseline else []

        # 2. Cargar estado previo (acumulado)
        prev_status = self.get_status()
        accumulated = set(prev_status.get("files_changed", []))

        # 3. Acumular: unión de cambios anteriores + nuevos
        accumulated.update(new_changes)
        accumulated_list = sorted(accumulated)

        # 4. Computar fingerprint actual del código (desde snapshot, sin re-walk)
        current_fingerprint = self.compute_fingerprint(current)

        # 5. Verificar contra CPG: si fingerprint coincide, CPG es espejo
        cpg_fingerprint = self._read_cpg_fingerprint()
        cpg_is_mirror = bool(cpg_fingerprint) and current_fingerprint == cpg_fingerprint

        if cpg_is_mirror:
            # CPG refleja exactamente el código actual → limpiar acumulados
            accumulated_list = []
            cpg_status = "up_to_date"
        else:
            cpg_status = "stale" if accumulated_list else prev_status.get("cpg_status", "up_to_date")

        # 6. Escribir cambio_detected.json
        change_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "files_changed": accumulated_list[:MAX_CHANGED_FILES],
            "total_changes": len(accumulated_list),
            "new_changes": new_changes[:MAX_CHANGED_FILES],       # delta para rebuild incremental
            "fingerprint": current_fingerprint,
            "ckg_status": prev_status.get("ckg_status", "up_to_date"),
            "cpg_status": cpg_status,
        }

        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.change_path, "w") as f:
            json.dump(change_data, f, indent=2)

        # 7. Registrar file_impact en el TraceGraph
        if new_changes:
            self._trace_file_impact(new_changes, cpg_status)

        return change_data

    # ── TraceGraph: registrar escrituras y estados ──────────────────

    def _trace_file_impact(self, files: list[str], cpg_status: str) -> None:
        """Registra un evento file_impact en el TraceGraph.

        Se invoca cuando el watcher detecta archivos modificados.
        El storage canonical es CognitiveGraph (indice trace).
        """
        try:
            from libs.cognitive_graph import CognitiveGraph
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc).isoformat()
            event_id = f"ev-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
            cg = CognitiveGraph(str(self.project_root))
            cg.store(
                "trace",
                event_id,
                {
                    "event_type": "file_impact",
                    "source": "walker_watcher",
                    "agent_id": os.environ.get("WALKER_AGENT_ID", ""),
                    "session_id": os.environ.get("WALKER_SESSION_ID", ""),
                    "sprint_id": "",
                    "task_id": "",
                    "node": "",
                    "sub_state": "",
                    "tool": "FileWatcher",
                    "files": files[:MAX_CHANGED_FILES],
                    "timestamp": now,
                    "started_at": now,
                    "finished_at": now,
                    "duration_ms": 0,
                    "metadata": {},
                    "data": {
                        "total_files": len(files),
                        "cpg_status": cpg_status,
                    },
                },
            )
        except Exception:
            pass  # best-effort: el watcher nunca debe fallar por tracing

    def _trace_state(self, index_type: str, status: str) -> None:
        """Registra un evento workflow_state en el TraceGraph.

        Se invoca en mark_indexed cuando un índice cambia de estado.
        """
        try:
            from libs.cognitive_graph import CognitiveGraph
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc).isoformat()
            event_id = f"ev-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
            cg = CognitiveGraph(str(self.project_root))
            cg.store(
                "trace",
                event_id,
                {
                    "event_type": "workflow_state",
                    "source": "walker_watcher",
                    "agent_id": os.environ.get("WALKER_AGENT_ID", ""),
                    "session_id": os.environ.get("WALKER_SESSION_ID", ""),
                    "sprint_id": "",
                    "task_id": "",
                    "node": "INDEX",
                    "sub_state": index_type,
                    "tool": "FileWatcher",
                    "files": [],
                    "timestamp": now,
                    "started_at": now,
                    "finished_at": now,
                    "duration_ms": 0,
                    "metadata": {},
                    "data": {
                        "index": index_type,
                        "status": status,
                    },
                },
            )
        except Exception:
            pass  # best-effort

    def get_status(self) -> dict[str, Any]:
        """Lee change_detected.json sin re-escanear."""
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
        """Marca un índice como up_to_date tras rebuild.

        Para CPG: verifica que el source_fingerprint almacenado en CPG
        coincida con el fingerprint actual. Si no coincide, NO limpia.
        """
        status = self.get_status()
        if index_type == "cpg":
            # Verificar contra fingerprint real del CPG
            cpg_fp = self._read_cpg_fingerprint()
            current_fp = status.get("fingerprint", "")
            if cpg_fp and cpg_fp == current_fp:
                # CPG es espejo → limpiar acumulados
                status["files_changed"] = []
                status["total_changes"] = 0
                status["cpg_status"] = "up_to_date"
            else:
                # Fingerprint no coincide → no limpiar
                status["cpg_status"] = "stale"
        elif index_type == "ckg":
            status["ckg_status"] = "up_to_date"
        else:
            status["cpg_status"] = "up_to_date"

        with open(self.change_path, "w") as f:
            json.dump(status, f, indent=2)

        # Registrar cambio de estado del índice en el TraceGraph
        self._trace_state(index_type, status.get(f"{index_type}_status", "up_to_date"))

    # ── Fingerprint ───────────────────────────────────────────────────

    def compute_fingerprint(self, snapshot: dict[str, str] | None = None) -> str:
        """SHA256 fingerprint del código fuente.

        Combina (rel_path + sha256_de_contenido) ordenadamente para
        producir un hash único del código. Este valor se almacena en
        CPG como source_fingerprint para verificar si CPG es espejo.

        Si se pasa snapshot (dict {rel: sha256}), lo usa directamente
        sin re-walk (ahorra I/O).
        """
        hasher = hashlib.sha256()
        if snapshot is not None:
            items = sorted(snapshot.items())
        else:
            items = sorted(self._compute_snapshot().items())
        for rel, file_hash in items:
            hasher.update(rel.encode("utf-8"))
            hasher.update(b"\x00")
            hasher.update(file_hash.encode("utf-8"))
            hasher.update(b"\x00")
        return hasher.hexdigest()

    def _read_cpg_fingerprint(self) -> str:
        """Lee el source_fingerprint del CPG, si existe."""
        for fname in ("cpg.json.gz", "cpg.json"):
            cpg_path = self.data_dir / fname
            if cpg_path.exists():
                try:
                    if fname.endswith(".gz"):
                        with gzip.open(cpg_path) as f:
                            data = json.load(f)
                    else:
                        with open(cpg_path) as f:
                            data = json.load(f)
                    return data.get("source_fingerprint", "") or ""
                except Exception:
                    pass
        return ""

    # ── Resolución de directorio de datos ────────────────────────────

    @staticmethod
    def _resolve_data_dir(project_root: str) -> Path:
        try:
            from features.project.application.service import ProjectService
            result = ProjectService.resolve_from_registry(start=project_root)
            if result:
                pid = result["project_id"]
                repo = result["repo_path"]
                candidate = Path(repo) / ".cognitive" / "projects" / pid / "data"
                if candidate.is_dir():
                    return candidate
        except Exception:
            pass
        cognitive_data = Path(project_root) / ".cognitive" / "projects"
        if cognitive_data.is_dir():
            pids = [d for d in cognitive_data.iterdir() if d.is_dir()]
            if pids:
                return pids[0] / "data"
        return Path(project_root) / "data"

    # ── Snapshot (hashes SHA256 por archivo + fingerprint combinado) ──

    def _compute_snapshot(self) -> dict[str, str]:
        """Walk watch dirs, compute SHA256 per file.

        Returns dict {rel_path: sha256_hex} — mismos hashes que usa
        CPG para source_fingerprint, por lo que la comparación directa
        entre fingerprint del watcher y source_fingerprint del CPG funciona.
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
                        with open(fpath, "rb") as f:
                            snapshot[rel] = hashlib.sha256(f.read()).hexdigest()
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
