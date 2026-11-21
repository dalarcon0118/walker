"""test_request_fidelity_bdd.py — ¿el engine registra el pedido del usuario?

El funnel empieza por el insumo: si la request no se persiste, la spec sólo
hereda la paráfrasis del agente. El reporte mide esa distancia; estos tests fijan
las funciones puras (tokens, placeholder, solapamiento y agregación).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "request_fidelity.py"
_spec = importlib.util.spec_from_file_location("request_fidelity", _SCRIPT)
fidelity = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fidelity)


def test_overlap_alto_si_el_objetivo_cita_el_pedido():
    pedido = "arreglá el login sin romper los tests de auth"
    objetivo = "Arreglar el login de auth sin romper los tests"

    assert fidelity.overlap(pedido, objetivo) >= 0.5


def test_overlap_cero_si_no_comparten_nada():
    assert fidelity.overlap("arreglá el login", "renombrar el botón") == 0.0


def test_un_pedido_vacio_no_se_puede_medir():
    assert fidelity.overlap("", "cualquier cosa") == 0.0


def test_el_placeholder_del_bind_se_detecta():
    assert fidelity.is_placeholder("sesión ses_e0640f75bffe66PA7826KyIGO0")
    assert not fidelity.is_placeholder("arreglá el login")


def test_resumen_separa_lo_medible_de_lo_que_no_tiene_request():
    pairs = [
        {"session": "s1", "request": "arreglá el login", "objective": "arreglar login",
         "placeholder": False, "overlap": 0.8},
        {"session": "s2", "request": "sesión s2", "objective": "x",
         "placeholder": True, "overlap": 0.0},
        {"session": "s3", "request": "migrar el storage", "objective": "cambiar el botón",
         "placeholder": False, "overlap": 0.05},
    ]

    out = fidelity.summarize_pairs(pairs)

    assert out["pairs"] == 3
    assert out["measurable"] == 2
    assert out["without_request"] == 1
    assert out["buckets"] == {"bajo": 1, "medio": 0, "alto": 1}
    assert [p["session"] for p in out["worst"]] == ["s3", "s1"]


def test_sin_pares_medibles_lo_declara():
    out = fidelity.summarize_pairs([])

    assert out["measurable"] == 0
    assert out["mean"] is None
    assert out["worst"] == []
