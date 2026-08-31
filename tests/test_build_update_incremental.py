"""
tests/test_build_update_incremental.py — Contrato de update() incremental.

FIX (rebuild innecesario): build_code_index forzaba {"build": True} y el
fallback silencioso del watcher convertía cualquier error en rebuild completo.
Este suite congela el contrato:

  1. update() con N archivos cambiados re-indexa EXACTAMENTE esos N archivos
     (nunca un rescan total: build() no debe invocarse).
  2. update() sin cambios y fingerprint al día retorna el CPG cacheado.
  3. Si el watcher falla, el fallback a build() queda VISIBLE en el
     resultado (clave "warnings") — nunca más silencioso.
"""
from types import SimpleNamespace

from features.code_analysis.application.build_service import BuildGraphUseCase


def _make_self(watcher_cls, cached: dict):
    """Self mínimo para invocar update() sin infraestructura real."""
    return SimpleNamespace(
        repo_path="/tmp/fake-repo",
        code_root=None,
        data_dir=None,
        project_id="proj-test",
        _repo=SimpleNamespace(load=lambda pid: cached),
        index_task_files=lambda files: {
            "ok": True,
            "files_updated": len(files),
            "files": list(files),
        },
        build=lambda: (_ for _ in ()).throw(
            AssertionError("build() NO debe llamarse en el camino incremental")
        ),
        _watcher_cls=watcher_cls,
    )


def _patch_watcher(monkeypatch, watcher_cls):
    # update() importa FileWatcher dentro de la función desde features.project
    import features.project as project_pkg

    monkeypatch.setattr(project_pkg, "FileWatcher", watcher_cls, raising=True)


class FakeWatcherOk:
    """Watcher que reporta exactamente 1 archivo cambiado."""

    def __init__(self, *a, **kw):
        pass

    def scan(self):
        return {"files_changed": ["src/nuevo.py"], "cpg_status": "stale"}

    def mark_indexed(self, kind):
        assert kind == "cpg"


class FakeWatcherClean:
    def __init__(self, *a, **kw):
        pass

    def scan(self):
        return {"files_changed": [], "cpg_status": "up_to_date"}

    def mark_indexed(self, kind):  # pragma: no cover - no debe llamarse
        raise AssertionError("mark_indexed no aplica sin cambios")


def test_update_reindexa_solo_los_archivos_cambiados(monkeypatch):
    """1 archivo cambiado → files_updated=1; build() jamás se invoca."""
    calls = {}
    cached = {"nodes": {}, "stats": {"total_nodes": 42}}

    self = _make_self(FakeWatcherOk, cached)
    original = self.index_task_files

    def spy(files):
        calls["files"] = files
        return original(files)

    self.index_task_files = spy
    _patch_watcher(monkeypatch, FakeWatcherOk)

    result = BuildGraphUseCase.update(self)

    assert calls["files"] == ["src/nuevo.py"]
    assert result["ok"] is True
    assert result["files_updated"] == 1
    assert "warnings" not in result  # camino feliz: sin fallbacks


def test_update_sin_cambios_retorna_cacheado_y_no_toca_nada(monkeypatch):
    cached = {"nodes": {"a": 1}, "stats": {"total_nodes": 7}}
    self = _make_self(FakeWatcherClean, cached)
    self.index_task_files = lambda files: (_ for _ in ()).throw(
        AssertionError("sin cambios no debe re-indexar nada")
    )
    _patch_watcher(monkeypatch, FakeWatcherClean)

    result = BuildGraphUseCase.update(self)

    assert result is cached


def test_fallback_de_watcher_es_visible_en_el_resultado(monkeypatch):
    """Si FileWatcher explota, hay build() pero CON warning visible."""
    class BrokenWatcher:
        def __init__(self, *a, **kw):
            raise RuntimeError("watcher roto a propósito")

    built = {"stats": {"total_nodes": 100}}
    self = _make_self(BrokenWatcher, cached={})
    self.build = lambda: dict(built)  # build real devuelve copia del doc
    # El import dentro de update() es from features.project import FileWatcher;
    # con la clase rota, la excepción ocurre en el constructor (dentro del try).

    _patch_watcher(monkeypatch, BrokenWatcher)

    result = BuildGraphUseCase.update(self)

    assert result["stats"]["total_nodes"] == 100
    warnings = result.get("warnings", [])
    assert any("fallback a build completo" in w for w in warnings)
    assert any("RuntimeError" in w for w in warnings)


def test_task_build_code_index_usa_update_por_defecto():
    """Contrato ACTUALIZADO (arquitectura L2, task dedicada): la rutina
    incremental vive en update_incremental_code; build_code_index es rebuild
    completo y deriva la rutina a ella (sin sync inline)."""
    import inspect as _inspect

    from shared.tasks.actions.initialize_project import (
        build_code_index,
    )
    from shared.tasks.actions.update_incremental_code import (
        update_incremental_code,
    )

    assert "update_incremental_code" in _inspect.getsource(build_code_index), (
        "build_code_index debe derivar la rutina incremental a su task dedicada"
    )
    assert '"action": "update"' in _inspect.getsource(update_incremental_code), (
        "la task dedicada debe pedir action=update al inspect_codebase"
    )


def test_metadata_descubrible_el_parametro_force_se_ensena():
    """CONTRATO DE DESCUBRIBILIDAD (actualizado a task dedicada):
    - update_incremental_code declara project_id en su schema (multi-proyecto)
      y su resultado enseña la vía de escape al rebuild completo (next_step).
    Un parámetro/ruta que el agente no puede descubrir es un parámetro muerto."""
    from shared.core.engine.infrastructure.task_registry import InMemoryTaskRegistry
    from shared.tasks.actions.update_incremental_code import register

    registry = InMemoryTaskRegistry()
    register(registry)

    info = registry.get_task_info("update_incremental_code")
    assert info, "update_incremental_code debe estar registrada"

    schema_props = (info.get("input_schema") or {}).get("properties", {})
    assert "project_id" in schema_props, "schema debe declarar project_id"

    qinstructions = info.get("query_instructions", "")
    assert "build_code_index" in qinstructions, (
        "query_instructions debe enseñar cuándo usar el rebuild completo"
    )

    # La vía de escape al full rebuild se enseña EN EL RESULTADO (runtime):
    from shared.tasks.actions.inspect_codebase import _dispatch_action

    import inspect as _inspect

    assert "force" in _inspect.getsource(_dispatch_action), (
        "el dispatch de update debe auto-enseñar build_code_index {force: true}"
    )
