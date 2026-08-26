"""tests/test_bootstrap_discovery_returns.py — Discovery de bootstrap en patrón returns.

Feature value: el discovery de tasks/providers NO traga excepciones en
silencio (gap SPEC-005). Ante un módulo cuyo register() lanza, se emite un
warning (convention-001) y el resto del package sigue registrándose — el
fallo queda observable, no oculto.
"""
from __future__ import annotations

import logging
import sys


def _make_tmp_package(tmp_path, with_bad: bool = True) -> str:
    """Crea un package temporal con módulos de prueba.

    with_bad=True incluye un módulo cuyo register() lanza (para probar el
    camino de warning del gap SPEC-005).
    """
    pkg = tmp_path / "fake_bootstrap_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "good_module.py").write_text(
        "def register(registry):\n    registry.append('good')\n"
    )
    if with_bad:
        (pkg / "bad_module.py").write_text(
            "def register(registry):\n    raise RuntimeError('boom del registro')\n"
        )
    sys.path.insert(0, str(tmp_path))
    return "fake_bootstrap_pkg"


def test_discover_logs_warning_y_continua(tmp_path, caplog):
    """Un módulo que falla al registrar emite warning; el bueno se registra igual."""
    from shared.core.engine.bootstrap import _discover_and_register

    pkg_name = _make_tmp_package(tmp_path)
    try:
        registry: list = []
        with caplog.at_level(logging.WARNING, logger="core.engine.bootstrap"):
            _discover_and_register("register", registry, pkg_name)
        # El módulo sano se registra aunque el vecino falle (bootstrap resiliente).
        assert registry == ["good"], f"se registro: {registry}"
        # El fallo es observable (warning), no se traga en silencio.
        messages = [r.message for r in caplog.records]
        assert any("bad_module" in m for m in messages), messages
        assert any("boom del registro" in m for m in messages), messages
    finally:
        sys.path.remove(str(tmp_path))
        for mod in (
            "fake_bootstrap_pkg",
            "fake_bootstrap_pkg.good_module",
            "fake_bootstrap_pkg.bad_module",
        ):
            sys.modules.pop(mod, None)


def test_discover_registra_todos_los_modulos_sanos(tmp_path, caplog):
    """Sin fallos, no se emiten warnings y todos los módulos se registran."""
    from shared.core.engine.bootstrap import _discover_and_register

    pkg_name = _make_tmp_package(tmp_path, with_bad=False)
    try:
        registry: list = []
        with caplog.at_level(logging.WARNING, logger="core.engine.bootstrap"):
            _discover_and_register("register", registry, pkg_name)
        assert registry == ["good"]
        assert [r.message for r in caplog.records] == []
    finally:
        sys.path.remove(str(tmp_path))
        for mod in (
            "fake_bootstrap_pkg",
            "fake_bootstrap_pkg.good_module",
            "fake_bootstrap_pkg.bad_module",
        ):
            sys.modules.pop(mod, None)
