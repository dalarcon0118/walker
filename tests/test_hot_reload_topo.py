"""
tests/test_hot_reload_topo.py — Orden topológico del hot-reload (raíz del hazard).

FIX isinstance entre identidades: la iteración cruda de sys.modules podía
recargar un dependiente ANTES que su dependencia, dejando `from X import Cls`
bindeado a la clase vieja mientras X ya exponía la nueva (bug de producción:
"'TaskResult' object has no attribute 'unwrap'").

Este suite usa paquetes REALES (importables y recargables) bajo el prefijo
monkeyparcheado `features_dyn.`:

  1. Cadena a←b←c importada en orden inverso se recarga a,b,c (topo, no
     orden histórico de import).
  2. La identidad de clases sobrevive al reload (isinstance verde cruzando
     módulos) — el bug de producción, congelado como contrato.
  3. Un importador cuyo archivo NO cambió entra al plan por cierre y
     rebindea sus from-imports.
  4. Un ciclo no cuelga: los residuales de Kahn se agregan deterministas.
"""
import importlib
import sys

import pytest

from protocols.cli.cli_interface.infrastructure import reloader

PREFIX = "features_dyn."


@pytest.fixture()
def dyn_pkg(tmp_path, monkeypatch):
    """Paquete real features_dyn con cadena a←b←c (+ ciclo opcional)."""
    pkg = tmp_path / "features_dyn"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("class Widget:\n    pass\n\n\nVAL = 1\n")
    (pkg / "b.py").write_text(
        "from features_dyn.a import VAL, Widget\n\n\nclass Gadget(Widget):\n    pass\n"
    )
    (pkg / "c.py").write_text("from features_dyn.b import Gadget\n")

    monkeypatch.syspath_prepend(str(tmp_path))
    # Importar en ORDEN INVERSO para envenenar el orden histórico de
    # sys.modules (así el test demuestra que manda el grafo, no la inserción).
    importlib.import_module("features_dyn.c")
    importlib.import_module("features_dyn.b")
    importlib.import_module("features_dyn.a")

    monkeypatch.setattr(reloader, "_FEATURES_PREFIX", PREFIX)
    monkeypatch.setattr(reloader, "_FEATURES_DIR", str(tmp_path))
    monkeypatch.setattr(reloader, "_reload_cache", {})
    monkeypatch.setattr(reloader, "_last_reload_ts", 0.0)
    monkeypatch.setattr(reloader, "_MIN_RELOAD_INTERVAL", 0.0)
    yield pkg

    # Limpiar TAMBIÉN el paquete raíz ("features_dyn" no matchea el prefijo
    # con punto): si sobrevive, su __path__ apunta al tmp viejo y los tests
    # siguientes importan silenciosamente desde allí (pollution).
    for name in [n for n in sys.modules if n == "features_dyn" or n.startswith(PREFIX)]:
        del sys.modules[name]


def _spy_reload_order(monkeypatch) -> list[str]:
    real = reloader._reload_module_core
    order: list[str] = []

    def spy(mod):
        order.append(mod.__name__)
        return real(mod)

    monkeypatch.setattr(reloader, "_reload_module_core", spy)
    return order


def _touch_all(pkg):
    for f in pkg.glob("*.py"):
        with open(f, "ab") as fh:
            fh.write(b"\n# touched")


def test_recarga_en_orden_topologico(dyn_pkg, monkeypatch):
    """Cadena a←b←c importada al revés → plan a,b,c (dependencias primero)."""
    order = _spy_reload_order(monkeypatch)
    _touch_all(dyn_pkg)

    assert reloader.reload_features_if_changed() is True
    filtered = [n for n in order if n.split(".")[-1] in {"a", "b", "c"}]
    assert filtered == [
        "features_dyn.a",
        "features_dyn.b",
        "features_dyn.c",
    ], f"orden histórico filtró mal: {filtered}"


def test_identidad_de_clases_preservada(dyn_pkg, monkeypatch):
    """El bug de producción, congelado: isinstance cruza módulos tras reload."""
    _touch_all(dyn_pkg)
    assert reloader.reload_features_if_changed() is True

    a = importlib.import_module("features_dyn.a")
    b = importlib.import_module("features_dyn.b")
    c = importlib.import_module("features_dyn.c")
    # Rebinding correcto cruzando la cadena:
    assert b.Widget is a.Widget
    assert c.Gadget is b.Gadget
    # Prueba ácida: instancia de la clase nueva pasa isinstance del símbolo
    # rebindeado (antes fallaba entre identidades viejas/nuevas).
    assert isinstance(a.Widget(), b.Widget)


def test_importador_no_cambiado_entra_al_plan(dyn_pkg, monkeypatch):
    """Cambia SOLO a.py → b y c se recargan por cierre y quedan coherentes."""
    target = dyn_pkg / "a.py"
    with open(target, "ab") as fh:
        fh.write(b"\nVAL = 2\n")
    assert reloader.reload_features_if_changed() is True

    a = importlib.import_module("features_dyn.a")
    b = importlib.import_module("features_dyn.b")
    assert a.VAL == 2
    assert b.Widget is a.Widget  # rebindeado aunque b.py no cambió


def test_orden_real_no_alfabetico(dyn_pkg, monkeypatch):
    """Test DISCRIMINANTE: dependencia 'm' debe recargarse antes que su
    importador 'f' — el orden alfabético daría [f,m] (incorrecto).
    Congela que el grafo AST manda, no la coincidencia alfabética."""
    (dyn_pkg / "m.py").write_text("class W:\n    pass\n")
    (dyn_pkg / "f.py").write_text("from features_dyn.m import W\n\n\nclass F(W):\n    pass\n")
    # Importar f primero para envenenar también el orden de inserción.
    importlib.import_module("features_dyn.f")

    order = _spy_reload_order(monkeypatch)
    with open(dyn_pkg / "f.py", "ab") as fh:
        fh.write(b"\n# touched")
    with open(dyn_pkg / "m.py", "ab") as fh:
        fh.write(b"\n# touched")

    assert reloader.reload_features_if_changed() is True
    pair = [n for n in order if n.split(".")[-1] in {"m", "f"}]
    assert pair == ["features_dyn.m", "features_dyn.f"], pair
    import features_dyn.m as m_mod
    import features_dyn.f as f_mod

    assert f_mod.W is m_mod.W


def test_ciclo_no_cuelga(dyn_pkg, monkeypatch):
    """Ciclo sobreviviente en runtime (import + atributo, no from-import):
    x↔y mutuos. Kahn completa; residuales agregados de forma determinista."""
    (dyn_pkg / "x.py").write_text(
        "import features_dyn.y as _y\n\nXV = 1\n\n\ndef get_yv():\n    return _y.YV\n"
    )
    (dyn_pkg / "y.py").write_text("import features_dyn.x as _x\n\nYV = 2\n")
    importlib.import_module("features_dyn.y")

    order = _spy_reload_order(monkeypatch)
    _touch_all(dyn_pkg)

    ok = reloader.reload_features_if_changed()  # debe completar, no colgar
    assert ok is True
    touched = [n for n in order if n.split(".")[-1] in {"x", "y"}]
    assert sorted(touched) == ["features_dyn.x", "features_dyn.y"]
    # El ciclo quedó funcional tras la recarga:
    import features_dyn.x as x_mod

    assert x_mod.get_yv() == 2
