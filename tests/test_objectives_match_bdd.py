"""Deduplicación de objetivos de tareas (4D Duplicidad).

El sprint acumuló copias con el mismo objetivo y texto distinto. La
comparación exacta no las ve; `objectives_match` normaliza y compara por
contención de tokens, con un umbral conservador para no fusionar tareas
distintas.
"""

from __future__ import annotations

from fast_mcp.shared_kernel.objectives import normalize_objective, objectives_match


def test_normaliza_acentos_puntuacion_y_mayusculas():
    assert normalize_objective("  Versionar  data/workflow — ¡Sí! ") == (
        "versionar data workflow si"
    )


def test_objetivo_truncado_es_el_mismo():
    assert objectives_match(
        "Versionar data/workflow",
        "Versionar data/workflow o eliminar la dependencia JSON en runtime; mover a codigo como artifacts.py",
    )


def test_objetivo_con_palabra_intercalada_es_el_mismo():
    assert objectives_match(
        "Propagar review_markdown en brief",
        "Propagar review_markdown en MCP brief: el agente debe ver el informe del gate desde brief",
    )


def test_tareas_distintas_no_se_fusionan():
    assert not objectives_match("arreglar resta", "arreglar suma")
    assert not objectives_match("x", "x y")


def test_objetivos_cortos_no_se_fusionan_por_subconjunto():
    assert not objectives_match("tests", "tests verdes de la suite completa")
