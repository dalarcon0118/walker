"""
tests/test_mcp_hot_reload_budget.py — Contrato del hot-reload del server MCP.

FIX (-32001 / cascadas repetidas / recomposición oculta): el loop stdio
recargaba features/ ANTES de cada request sin cooldown ni presupuesto, y la
primera tarea tras una cascada pagaba AppContainer.build() completo dentro de
su ventana de respuesta (timeout -32001 del cliente). Este suite congela:

  1. Cooldown: dos ráfagas de ediciones seguidas producen UNA sola cascada;
     la segunda queda diferida y se aplica en la ventana siguiente.
  2. No-consumo: un cambio detectado durante el defer NO se marca como
     visto — no se pierde aunque el archivo no vuelva a cambiar.
  3. Recomposición eager: _reset_server_cache deja el service locator
     utilizable (get_module_registry resuelve) sin esperar al request.
  4. Higiene: sin prints DEBUG en el dispatch Camino A.
"""
import importlib
import sys

import pytest

from features.main.cli_interface.infrastructure import reloader
# NOTA: `from core.engine import bootstrap` traería la FUNCIÓN bootstrap
# (expuesta en __init__) que hace sombra al submódulo. Importar símbolos.
from core.engine.bootstrap import get_module_registry, reset_module_registry


@pytest.fixture(autouse=True)
def _isolated_reload_state(monkeypatch, tmp_path):
    """Aísla estado global del reloader y apunta _FEATURES_DIR a un árbol tmp.

    Los .py del árbol tmp NO se importan a sys.modules, así que la cascada
    itera 0 módulos pero ejercita todo el orquestado (detección → cooldown →
    consumo de firmas → on_changed).
    """
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    monkeypatch.setattr(reloader, "_FEATURES_DIR", str(features_dir))
    monkeypatch.setattr(reloader, "_reload_cache", {})
    monkeypatch.setattr(reloader, "_last_reload_ts", 0.0)
    monkeypatch.setattr(reloader, "_reload_stats", {"cascades": 0, "deferred": 0})
    yield features_dir


def _touch(path, extra=b""):
    with open(path, "ab") as f:
        f.write(b"\n# touch" + extra)


def test_cooldown_una_sola_cascada_por_rafaga(_isolated_reload_state, monkeypatch):
    """Dos ráfagas seguidas → 1 cascada; fuera de cooldown → la 2ª aplica."""
    fired = []
    target = _isolated_reload_state / "mod_a.py"
    target.write_text("VALUE = 1\n")

    assert reloader.reload_features_if_changed(on_changed=lambda: fired.append(1)) is True
    assert len(fired) == 1

    # Ráfaga inmediata dentro del cooldown → diferida, sin cascada.
    _touch(target)
    assert reloader.reload_features_if_changed(on_changed=lambda: fired.append(2)) is False
    assert len(fired) == 1
    assert reloader._reload_stats["deferred"] == 1

    # Fuera de cooldown (reloj inyectado) → el cambio pendiente aplica.
    monkeypatch.setattr(
        reloader, "_now", lambda: reloader._last_reload_ts + reloader._MIN_RELOAD_INTERVAL + 0.01
    )
    assert reloader.reload_features_if_changed(on_changed=lambda: fired.append(3)) is True
    assert len(fired) == 2
    assert reloader._reload_stats["cascades"] == 2


def test_no_consume_cambio_durante_defer(_isolated_reload_state, monkeypatch):
    """El cambio detectado en cooldown no se marca visto: no se pierde."""
    target = _isolated_reload_state / "mod_b.py"
    target.write_text("VALUE = 1\n")
    assert reloader.reload_features_if_changed() is True

    _touch(target, extra=b"-v2")  # firma nueva, dentro del cooldown
    assert reloader.reload_features_if_changed() is False
    sig = reloader._stat_sig_core(str(target)).value_or((0, 0))
    assert reloader._reload_cache.get(str(target)) != sig  # NO consumida

    monkeypatch.setattr(
        reloader, "_now", lambda: reloader._last_reload_ts + reloader._MIN_RELOAD_INTERVAL + 0.01
    )
    assert reloader.reload_features_if_changed() is True
    assert reloader._reload_cache.get(str(target)) == sig  # consumida recién ahora


def test_reset_server_cache_recompone_locator_eager():
    """Tras invalidar, _reset_server_cache deja el locator utilizable YA."""
    from features.main.cli_interface.mcp_adapter import _reset_server_cache

    reset_module_registry()
    with pytest.raises(KeyError):
        get_module_registry()

    _reset_server_cache()
    registry = get_module_registry()
    assert registry is not None


def test_sin_debug_print_en_camino_a():
    """Contrato de higiene: el dispatch no contamina stderr con DEBUG."""
    import inspect

    from features.main.cli_interface import mcp_adapter

    source = inspect.getsource(mcp_adapter)
    assert "DEBUG _call_tool" not in source


def test_baseline_arranque_no_dispara_cascada(_isolated_reload_state):
    """Cold start: archivos pre-existentes al boot se siembran como vistos
    SIN recargar (el primer request del server nunca paga una cascada).
    Un cambio POSTERIOR al arranque sí dispara."""
    fired = []
    old_file = _isolated_reload_state / "preexistente.py"
    old_file.write_text("VALUE = 1\n")
    past = reloader._BOOT_TIME - 100  # mtime anterior al arranque del proceso
    import os as _os

    _os.utime(old_file, (past, past))

    assert reloader.reload_features_if_changed(on_changed=lambda: fired.append(1)) is False
    assert fired == []  # sin cascada
    assert str(old_file) in reloader._reload_cache  # pero sembrado como visto

    # Cambio posterior al boot → cascada normal.
    _touch(old_file)
    assert reloader.reload_features_if_changed(on_changed=lambda: fired.append(2)) is True
    assert len(fired) == 1
