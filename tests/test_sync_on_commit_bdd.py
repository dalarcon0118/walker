"""test_sync_on_commit_bdd.py — hook post-commit que mantiene el CPG al día.

Medido 2026-11-17: después de commitear `harness/runner.py`, el CPG seguía
sirviendo la versión vieja. `sync_code_index` solo lo invocan tareas de
workflow, así que editar+commitear no actualiza nada. El hook cierra ese
hueco; estos tests fijan sus dos garantías: nunca falla el commit y nunca
corren dos syncs en paralelo.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from scripts import sync_cpg_on_commit as hook


def test_lock_segundo_intento_se_salta(tmp_path):
    lock = tmp_path / "sync.lock"
    assert hook.acquire_lock(lock) is True
    assert hook.acquire_lock(lock) is False, "dos syncs en paralelo, no"

    hook.release_lock(lock)
    assert hook.acquire_lock(lock) is True


def test_lock_viejo_se_descarta(tmp_path):
    """Un lock de un proceso muerto no puede bloquear para siempre."""
    lock = tmp_path / "sync.lock"
    lock.write_text("999999 0\n")
    old = time.time() - hook.LOCK_STALE_S - 60
    os.utime(lock, (old, old))

    assert hook.acquire_lock(lock) is True


def test_run_sync_sin_proyecto_skipped(tmp_path, monkeypatch):
    called = {"sync": False}

    monkeypatch.setattr(hook, "resolve_project", lambda root: None)
    outcome = hook.run_sync(str(tmp_path), sync=lambda root: called.update(sync=True))

    assert outcome["status"] == "skipped"
    assert "sin proyecto registrado" in outcome["reason"]
    assert called["sync"] is False


def test_run_sync_ok_reporta_stats_y_libera_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "resolve_project", lambda root: {"project_id": "demo"})

    def fake_sync(root):
        return {"mode": "incremental", "result": {"stats": {
            "total_nodes": 6206, "total_edges": 14380, "files_scanned": 458,
        }}}

    outcome = hook.run_sync(str(tmp_path), sync=fake_sync)

    assert outcome["status"] == "ok"
    assert outcome["project_id"] == "demo"
    assert outcome["nodes"] == 6206 and outcome["edges"] == 14380
    assert not (tmp_path / "sync.lock").exists(), "el lock debe liberarse siempre"


def test_run_sync_error_no_propaga_y_libera_lock(tmp_path, monkeypatch):
    """Un CPG roto no puede romper el commit (el hook es best-effort)."""
    monkeypatch.setattr(hook, "resolve_project", lambda root: {"project_id": "demo"})

    def boom(root):
        raise RuntimeError("CPG corrupto")

    outcome = hook.run_sync(str(tmp_path), sync=boom)

    assert outcome["status"] == "error"
    assert "CPG corrupto" in outcome["error"]
    assert not (tmp_path / "sync.lock").exists()


def test_run_sync_respeta_la_variable_de_escape(tmp_path, monkeypatch):
    monkeypatch.setenv("WALKER_SYNC_DISABLED", "1")
    monkeypatch.setattr(hook, "resolve_project", lambda root: {"project_id": "demo"})

    outcome = hook.run_sync(str(tmp_path), sync=lambda root: {"mode": "full"})
    assert outcome == {"status": "skipped", "reason": "WALKER_SYNC_DISABLED=1"}


def test_run_sync_con_lock_tomado_se_salta(tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "resolve_project", lambda root: {"project_id": "demo"})
    lock = tmp_path / "sync.lock"
    lock.write_text("1 0\n")

    outcome = hook.run_sync(str(tmp_path), sync=lambda root: {"mode": "full"})
    assert outcome["status"] == "skipped"
    assert outcome["reason"] == "otro sync en curso"
    assert Path(lock).exists(), "no debe borrar el lock de otro proceso"


def test_run_sync_cuenta_nodos_del_grafo_cargado(tmp_path, monkeypatch):
    """`update()` devuelve el grafo, `build()` devuelve stats: el log no debe
    decir "None nodos" cuando el sync funcionó (visto en el primer hook real)."""
    monkeypatch.setattr(hook, "resolve_project", lambda root: {"project_id": "demo"})

    def fake_update(root):
        return {"mode": "incremental",
                "result": {"nodes": {"a": {}, "b": {}}, "edges": [1, 2, 3]}}

    outcome = hook.run_sync(str(tmp_path), sync=fake_update)
    assert outcome["nodes"] == 2
    assert outcome["edges"] == 3
