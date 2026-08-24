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
        code_paths=None,
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
    """La task pública NO debe forzar rebuild: default incremental, full solo con force."""
    import inspect as _inspect

    from features.main.task_execution.tasks.initialize_project import (
        build_code_index,
    )

    src = _inspect.getsource(build_code_index)
    assert '"action": "update"' in src, "default debe ser sync incremental"
    assert 'data.get("force")' in src, "rebuild completo requiere force explícito"


def test_metadata_descubrible_el_parametro_force_se_ensena():
    """CONTRATO DE DESCUBRIBILIDAD: un agente que nunca vio esta task debe poder
    descubrir 'force' SOLO desde help/discover. Si la metadata deja de enseñarlo,
    el parámetro muere (nadie invoca lo que no conoce)."""
    from core.engine.infrastructure.task_registry import InMemoryTaskRegistry
    from features.main.task_execution.tasks import initialize_project as mod

    registry = InMemoryTaskRegistry()
    mod.register(registry)

    info = registry.get_task_info("build_code_index")
    assert info, "build_code_index debe estar registrada"

    # 1) input_schema formal: la via machine-readable de descubrimiento
    schema_props = (info.get("input_schema") or {}).get("properties", {})
    assert "force" in schema_props, "input_schema.properties.force es obligatorio"
    assert "default" in schema_props["force"], "el default de force debe documentarse"

    # 2) example copiable: muestra la invocacion con force tal cual
    example = info.get("example", "")
    assert "force" in example, "el example debe mostrar la forma con force"

    # 3) query_instructions: cuando usar cada modo (guia de decision)
    qinstructions = info.get("query_instructions", "")
    assert "force" in qinstructions and "incremental" in qinstructions.lower()

    # 4) discover() público expone todo lo anterior (mismo camino que el agente)
    discovered = {t["name"]: t for t in registry.discover(scope="all")["tasks"]}
    entry = discovered["build_code_index"]
    assert entry["input_schema"] == info["input_schema"]
    assert entry["example"] == example
