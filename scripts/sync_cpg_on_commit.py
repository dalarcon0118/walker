"""scripts/sync_cpg_on_commit.py — sincroniza el CPG después de un commit.

Problema medido (2026-11-17): el CPG de walker se quedó con la versión vieja de
`harness/runner.py` aunque el commit estaba hecho. `sync_code_index` existe
"para no depender de que alguien se acuerde", pero solo lo invocan tareas de
workflow (PROJECT_INIT.SCAN_CODE / analyze.GATHER), así que editar y commitear
no toca el grafo. Este script es el disparador que faltaba: lo llama el hook
`post-commit` (ver `scripts/install_git_hooks.py`).

Decisiones:
- Usa `sync_project_cpg` (el caso de uso de la app, idempotente: build si está
  vacío, incremental si no) en vez de levantar el servidor MCP por stdio: el
  hook corre en cada commit y arrancar un server completo sería desproporcionado.
- Nunca falla ni bloquea: cualquier error se loguea y sale con 0 (un hook que
  rompe el commit es peor que un grafo desactualizado).
- Lock por proyecto: commits seguidos no lanzan dos syncs en paralelo; un lock
  viejo (>30 min, proceso muerto) se descarta.

Uso:
    python3 scripts/sync_cpg_on_commit.py            # desde la raíz del repo
    WALKER_SYNC_DISABLED=1 python3 scripts/...       # no hace nada
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

WALKER_ROOT = Path(__file__).resolve().parent.parent
if str(WALKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WALKER_ROOT))

LOCK_STALE_S = 30 * 60
LOCK_NAME = "sync.lock"
LOG_PATH = Path.home() / ".walker-backups" / "walker-sync.log"

logger = logging.getLogger("walker.sync")


def acquire_lock(path: Path, stale_s: int = LOCK_STALE_S) -> bool:
    """True si tomó el lock; False si ya hay un sync corriendo."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()} {time.time()}\n".encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return False
        if age < stale_s:
            return False
        logger.warning("lock viejo (%.0f min), se descarta: %s", age / 60, path)
        try:
            path.unlink()
        except OSError:
            return False
        return acquire_lock(path, stale_s)


def release_lock(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def resolve_project(root: str) -> dict | None:
    from fast_mcp.shared_kernel.project_support import ProjectService

    try:
        return ProjectService.resolve_from_registry(root)
    except Exception as exc:  # noqa: BLE001 — el hook nunca falla
        logger.warning("no se pudo resolver el proyecto de %s: %s", root, exc)
        return None


def read_stats(root: str) -> dict:
    """Stats reales del CPG (misma fuente que `sync_project_cpg.stats_before`).

    `update()` puede devolver {"ok": True, "files_updated": N} sin stats, y el
    log quedaba en "None nodos / None edges" aunque el sync hubiera tocado el
    grafo. Se lee el estado final en vez de adivinarlo.
    """
    try:
        from fast_mcp.features.code_analysis.application.services.cpg_service import (
            CpgService,
        )

        return CpgService.from_chain_dir(root).stats() or {}
    except Exception as exc:  # noqa: BLE001 — solo es logging
        logger.debug("no se pudieron leer stats de %s: %s", root, exc)
        return {}


def run_sync(root: str, sync=None, full: bool = False) -> dict:
    """Sincroniza el CPG del proyecto dueño de `root`.

    Devuelve {"status": "ok"|"skipped"|"error", ...} para poder testear sin
    depender del log ni del proceso.
    """
    if os.environ.get("WALKER_SYNC_DISABLED") == "1":
        return {"status": "skipped", "reason": "WALKER_SYNC_DISABLED=1"}
    if sync is None:
        from fast_mcp.features.code_analysis.application.use_cases.sync_cpg import (
            sync_project_cpg,
        )

        sync = sync_project_cpg

    detected = resolve_project(root)
    if not detected or not detected.get("project_id"):
        return {"status": "skipped", "reason": f"sin proyecto registrado para {root}"}

    project_id = detected["project_id"]
    try:
        from fast_mcp.shared_kernel.project_support import ProjectService

        paths = ProjectService.resolve_paths(root)
        lock_path = Path(str(paths.data_dir)) / LOCK_NAME
    except Exception:
        lock_path = Path(root) / LOCK_NAME

    if not acquire_lock(lock_path):
        return {"status": "skipped", "project_id": project_id,
                "reason": "otro sync en curso"}

    started = time.time()
    try:
        payload = (sync(root, refresh=True) if full else sync(root)) or {}
        result = (payload.get("result") if isinstance(payload, dict) else {}) or {}
        # `update()` devuelve {"ok": False, "error": "...", "scope": "task"} cuando
        # los archivos cambiados no son código (p. ej. un commit que solo toca
        # .gitignore o docs): no es un fallo, es un no-op. Antes se logueaba como
        # "ok · None nodos", que parece un sync roto.
        if isinstance(result, dict) and result.get("ok") is False:
            return {
                "status": "noop",
                "project_id": project_id,
                "reason": result.get("error") or "sin nodos que indexar",
            }
        # `build()` devuelve {"stats": {...}}; `update()` devuelve el grafo
        # cargado ({"nodes": {...}, "edges": [...]}). Sin el fallback, el log
        # decía "None nodos / None edges" aunque el sync hubiera funcionado.
        stats = (
            result.get("stats")
            or read_stats(root)
            or (payload.get("stats_before") if isinstance(payload, dict) else {})
            or {}
        )
        nodes = stats.get("total_nodes")
        if nodes is None and isinstance(result.get("nodes"), dict):
            nodes = len(result["nodes"])
        edges = stats.get("total_edges")
        if edges is None and isinstance(result.get("edges"), list):
            edges = len(result["edges"])
        return {
            "status": "ok",
            "project_id": project_id,
            "mode": (payload or {}).get("mode") if isinstance(payload, dict) else None,
            "nodes": nodes,
            "edges": edges,
            "files": stats.get("files_scanned"),
            "seconds": round(time.time() - started, 1),
        }
    except Exception as exc:  # noqa: BLE001 — el hook nunca falla
        return {"status": "error", "project_id": project_id, "error": str(exc)}
    finally:
        release_lock(lock_path)


def emit_indexed(root: str, outcome: dict) -> list:
    """Anuncia `cpg.indexed` para que otros hooks reaccionen (best-effort)."""
    try:
        from fast_mcp.shared_kernel.hooks import HookBus

        return HookBus.load(root).emit("cpg.indexed", {
            "project_id": outcome.get("project_id"),
            "mode": outcome.get("mode"),
            "nodes": outcome.get("nodes"),
            "edges": outcome.get("edges"),
            "files": outcome.get("files"),
        })
    except Exception as exc:  # noqa: BLE001 — nunca rompe el sync
        logger.debug("no se pudo emitir cpg.indexed: %s", exc)
        return []


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true",
                    help="build completo (el incremental es lossy por ids inestables)")
    args = ap.parse_args()

    root = str(WALKER_ROOT)
    outcome = run_sync(root, full=args.full)
    if outcome.get("status") == "ok":
        logger.info(
            "sync ok · %s · %s · %s nodos / %s edges · %ss",
            outcome.get("project_id"), outcome.get("mode", "?"),
            outcome.get("nodes"), outcome.get("edges"), outcome.get("seconds"),
        )
        emit_indexed(root, outcome)
    else:
        logger.info("sync %s · %s", outcome.get("status"), outcome.get("reason")
                    or outcome.get("error") or outcome.get("project_id"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
