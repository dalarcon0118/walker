"""
infrastructure/watcher.py — File change watcher.

SRP: Monitorea el repo_path del proyecto (scope completo, ADR-WALKER-054 D1),
     acumula cambios y verifica fingerprint del CPG para determinar si es
     espejo del código.

Layer: Infrastructure (pure IO — filesystem polling, snapshotting).
Does NOT import application layer or CognitiveGraph.
Tracing is delegated via an optional callback.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

IGNORE_FILENAME = ".walkerignore"

#: Estado que walker escribe en cada operación. Si entra al fingerprint del
#: código, la foto cambia sin que cambie una línea y el CPG se reporta `stale`
#: para siempre (medido 2026-11-26: 784 archivos en el fingerprint, 28 de
#: estado, y `data/metrics/tool_calls.jsonl` cambia en CADA llamada a tool).
_STATE_FILENAMES = frozenset({
    ".file_snapshot.json",
    "change_detected.json",
    "human_turns_report.json",
})
_STATE_SUFFIXES = (".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".jsonl")


def is_walker_state(rel_path: str) -> bool:
    """True si el path es estado del propio walker (nunca es código indexable)."""
    rel = str(rel_path).replace(os.sep, "/")
    name = rel.rsplit("/", 1)[-1]
    if name.startswith(".workflow-state"):
        return True
    if name in _STATE_FILENAMES:
        return True
    return rel.endswith(_STATE_SUFFIXES)
CHANGE_FILE = "change_detected.json"
MAX_CHANGED_FILES = 500
#: Snapshot por sesión (P1.13). El snapshot global es del PROYECTO: una sesión
#: nueva que diffea contra él hereda las escrituras de la anterior. Este se
#: establece en la primera lectura de la sesión.
SESSION_SNAPSHOT_PREFIX = ".file_snapshot.session-"
MAX_SESSION_SNAPSHOTS = 50


#: Archivos que escribe WALKER (no el agente). Sin este filtro, el diff del
#: scan marca `wrote` por el propio estado del motor: medido en el harness —
#: `idem` y `shellmut` pasaron a "mutaciones" por `.workflow-state.json` y
#: `data/cognitive.sqlite3`.
_WALKER_STATE_NAMES = frozenset(
    {
        "change_detected.json",
        ".file_snapshot.json",
        ".workflow-state.json",
        "cognitive.sqlite3",
        "ckg.json.gz",
        "delta.json.gz",
        "trace.json.gz",
        "cpg.json.gz",
    }
)


def _is_walker_state(rel_path: str) -> bool:
    parts = [p for p in str(rel_path).replace("\\", "/").split("/") if p]
    if not parts:
        return True
    if parts[0] == ".cognitive":
        return True
    name = parts[-1]
    if name in _WALKER_STATE_NAMES or name.endswith(".json.gz"):
        return True
    if name.startswith(SESSION_SNAPSHOT_PREFIX):
        return True
    # El WAL/SHM del sqlite también lo escribe el motor, no el agente:
    # `cognitive.sqlite3-wal` no termina en `.sqlite3` y se colaba en `touched`.
    if parts[0] == "data" and re.match(r".*\.sqlite3(-wal|-shm)?$", name):
        return True
    # Telemetría por tool (`data/metrics/*.jsonl`): la escribe el SERVER en cada
    # llamada MCP, no el agente. Sin este filtro, la primera tool que se llama
    # marca `wrote` y el gate de cierre nunca queda limpio aunque el agente no
    # haya tocado nada (medido 2026-11-20: `run_tests` reportó
    # `touched: ["data/metrics/tool_calls.jsonl"]`).
    if len(parts) >= 3 and parts[0] == "data" and parts[1] == "metrics":
        return True
    return False


def _session_key(session_id: str) -> str:
    """Nombre de archivo seguro para un session_id (sin path traversal)."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session_id or "")).strip(".")
    return (safe or "anon")[:120]


def changed_since_last_scan(project_root: str, *, session_id: str = "") -> list[str]:
    """Archivos cambiados desde el último scan (evidencia de escritura).

    `FileWatcher.scan()` compara el árbol con el snapshot previo. No sabe
    quién escribió, pero permite que una escritura que el hook no observó
    (subagente, shell del host, `sed -i`, `git apply`) no quede invisible:
    la sesión queda con deuda de verificación en vez de cerrar limpia.

    Con `session_id` el diff es contra el snapshot PROPIO de la sesión, no el
    del proyecto. El global se comparte entre sesiones: usarlo para decidir si
    ESTA sesión escribió le atribuye los cambios de la anterior. La primera
    lectura de una sesión establece su baseline y devuelve `[]`.

    Fail-open: si el scan falla, devuelve [] y no se inventa evidencia.
    """
    root = str(project_root or "")
    if not root or not os.path.isdir(root):
        return []
    try:
        return FileWatcher(root).changes_since(session_id=session_id)
    except Exception:  # noqa: BLE001 — la evidencia no rompe la tool
        return []


def seed_session_baseline(project_root: str, *, session_id: str) -> bool:
    """Fija el baseline de escritura de una sesión (no lo refresca).

    Se llama al ABRIR/VINCULAR la sesión: si el baseline se creara recién en el
    primer `session_context`, las escrituras hechas antes (subagente, shell del
    host, `sed -i`) quedaban absorbidas por ese baseline y no dejaban deuda.
    Idempotente: si el snapshot ya existe, no lo toca (no absorbe cambios).

    Fail-open: devuelve False (sin baseline nuevo) si no se pudo escribir.
    """
    root = str(project_root or "")
    sid = str(session_id or "").strip()
    # "mcp" es el canal anónimo: un baseline compartido contaminaría sesiones.
    if not root or not sid or sid == "mcp" or not os.path.isdir(root):
        return False
    try:
        return FileWatcher(root).seed_session_baseline(sid)
    except Exception:  # noqa: BLE001 — la evidencia no rompe session_open/bind
        return False


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
        """True si el path relativo cae bajo algún patrón del .walkerignore.

        Semántica gitignore (corregida 2026-11-16). ANTES había dos agujeros:

        - `patron/` (directorio) solo matcheaba la cadena EXACTA con barra
          final: `is_ignored("docs/spec.md")` era False aunque `docs/`
          estuviera en el archivo. Solo el podado de directorios del walk
          evitaba indexar el contenido, así que cualquier consumidor que
          filtre por ARCHIVO se tragaba el directorio ignorado en silencio.
        - Un patrón sin barra (`*.pyc`, `.env*`) solo matcheaba en la raíz,
          no a cualquier profundidad.

        Un patrón de directorio aplica al directorio y a TODA su descendencia,
        a cualquier nivel (igual que el podado, que evalúa por basename).
        """
        rel = str(rel_path).replace(os.sep, "/")
        if rel.startswith("./"):
            rel = rel[2:]
        segments = [s for s in rel.split("/") if s]
        for pattern in self._patterns:
            pattern = pattern.replace(os.sep, "/")
            if pattern.endswith("/"):
                base = pattern.rstrip("/")
                if "/" in base:
                    # Patrón ANIDADO (`lib/es6/`): se compara contra el path
                    # relativo completo, no por segmento. Antes caía en la rama
                    # de basename y nunca matcheaba (`es6` != `lib/es6`).
                    if rel == base or rel.startswith(base + "/"):
                        return True
                    continue
                if base and any(fnmatch.fnmatch(seg, base) for seg in segments):
                    return True
                continue
            if fnmatch.fnmatch(rel, pattern):
                return True
            if "/" not in pattern and segments and fnmatch.fnmatch(segments[-1], pattern):
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
        *,
        data_dir: str | None = None,
        cpg_path: str | None = None,
        on_trace_event: Callable[[str, dict], None] | None = None,
        cpg_fingerprint_reader: Callable[[], str] | None = None,
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
        # Inyección DI (ADR-WALKER-055 F1): lector del fingerprint del CPG
        # proveído por el feature que posee el dominio cpg (code_analysis).
        # Este watcher NO conoce el formato del CPG — recibe el valor ya
        # resuelto (default None → sin fingerprint → mirror no verificable).
        self._cpg_fingerprint_reader = cpg_fingerprint_reader

        # Scope = repo_path completo (ADR-WALKER-054 D1: sin code_paths)
        self.watch_dirs = [self.project_root]
        # Lista completa del último scan (sin truncar). `update()` la consume.
        self.last_changes_all: list[str] = []

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

        # Lista COMPLETA en memoria (el JSON se trunca a MAX_CHANGED_FILES solo
        # para no crecer sin límite). Bug medido 2026-11-17: `update()` consumía
        # el JSON truncado y ordenado alfabéticamente, así que con >500 cambios
        # los archivos que caían después del corte (p. ej. `harness/runner.py`)
        # NUNCA se indexaban; y como `index_task_files` guarda el fingerprint de
        # TODO el árbol, `mark_indexed` declaraba "up_to_date" y vaciaba la cola.
        # Divergencia permanente y silenciosa: el CPG quedaba con código viejo.
        self.last_changes_all = list(accumulated_list)

        change_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "files_changed": accumulated_list[:MAX_CHANGED_FILES],
            "total_changes": len(accumulated_list),
            "truncated": len(accumulated_list) > MAX_CHANGED_FILES,
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

    def changes_since(self, session_id: str = "") -> list[str]:
        """Diff del árbol contra el snapshot de ESTA sesión (P1.13).

        Read-only respecto al estado del watcher: NO toca `change_detected.json`
        ni los índices del CPG. Sin `session_id` usa el snapshot global (igual
        que `scan()`); con `session_id` usa `.file_snapshot.session-<sid>.json`,
        que se establece en la primera lectura y sólo cuenta lo que cambió
        después — una sesión nueva no hereda las escrituras de la anterior.

        El footprint del motor (`.cognitive/`, `*json.gz`, snapshots, sqlite)
        no cuenta: no lo escribió el agente.
        """
        path = (
            self._session_snapshot_path(session_id)
            if session_id
            else self.snapshot_path
        )
        previous = self._load_snapshot(path)
        current = self._compute_snapshot()

        # Formato viejo (SHA256 de contenido) vs actual (size:mtime): comparar
        # reportaría todo el árbol como cambiado.
        if previous and current:
            prev_sample = next(iter(previous.values()))
            curr_sample = next(iter(current.values()))
            if len(prev_sample) == 64 and ":" in curr_sample:
                previous = {}

        # Re-escribir 50KB por lectura sería churn: solo si cambió.
        if not previous or current != previous:
            self._save_snapshot(current, path)
        if session_id:
            self._prune_session_snapshots()
        if not previous:
            return []
        return [
            f
            for f in sorted(self._diff(previous, current))
            if not _is_walker_state(f) and not self._ignore.is_ignored(f)
        ]

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
        """Devuelve el fingerprint del CPG provisto por DI (si está inyectado).

        ADR-WALKER-055 F1: este watcher NO parsea el archivo del CPG ni
        conoce su formato — el feature code_analysis provee el valor via
        `cpg_fingerprint_reader` (composition root).
        """
        if self._cpg_fingerprint_reader is None:
            return ""
        try:
            return str(self._cpg_fingerprint_reader() or "")
        except Exception:
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
                    if self._ignore.is_ignored(rel) or is_walker_state(rel):
                        continue
                    # El data_dir canónico (o el legacy <repo>/data) es estado,
                    # no fuente: si entra al fingerprint, se auto-invalida en
                    # cada scan (ver _watcher_fingerprint_core).
                    if self._is_data_path(fpath):
                        continue
                    try:
                        stat = os.stat(fpath)
                        snapshot[rel] = f"{stat.st_size}:{stat.st_mtime_ns}"
                    except (OSError, PermissionError):
                        continue
        return snapshot

    def _is_data_path(self, fpath: str) -> bool:
        """True si el archivo vive bajo el data_dir del watcher (estado)."""
        try:
            data_root = os.path.abspath(str(self.data_dir))
        except Exception:  # noqa: BLE001 — sin data_dir no hay nada que excluir
            return False
        target = os.path.abspath(fpath)
        return target == data_root or target.startswith(data_root + os.sep)

    def _load_snapshot(self, path: Path | None = None) -> dict[str, str]:
        target = path or self.snapshot_path
        if target.exists():
            try:
                with open(target) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_snapshot(
        self, snapshot: dict[str, str], path: Path | None = None
    ) -> None:
        target = path or self.snapshot_path
        os.makedirs(target.parent, exist_ok=True)
        with open(target, "w") as f:
            json.dump(snapshot, f, indent=2 if target == self.snapshot_path else None)

    def _session_snapshot_path(self, session_id: str) -> Path:
        return self.data_dir / f"{SESSION_SNAPSHOT_PREFIX}{_session_key(session_id)}.json"

    def seed_session_baseline(self, session_id: str) -> bool:
        """Crea el snapshot de la sesión si no existe; nunca lo refresca."""
        path = self._session_snapshot_path(session_id)
        if path.exists():
            return False
        self._save_snapshot(self._compute_snapshot(), path)
        self._prune_session_snapshots()
        return True

    def _prune_session_snapshots(self) -> None:
        """Borra los snapshots de sesión más viejos (no crecen sin límite)."""
        try:
            files = sorted(
                self.data_dir.glob(f"{SESSION_SNAPSHOT_PREFIX}*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for stale in files[MAX_SESSION_SNAPSHOTS:]:
                stale.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _diff(previous: dict, current: dict) -> list[str]:
        changed = []
        all_keys = set(previous) | set(current)
        for key in all_keys:
            if previous.get(key) != current.get(key):
                changed.append(key)
        return changed
