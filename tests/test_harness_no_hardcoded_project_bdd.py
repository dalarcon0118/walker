"""test_harness_no_hardcoded_project_bdd.py — el harness no se ancla (P1.7).

El harness tenía `game_reset` escrito en cada comando y una ruta absoluta al
repo de walker. El plugin resuelve su proyecto desde `ctx.location.directory`,
así que correr la suite desde otro directorio hacía que el plugin mirara un
proyecto y los comandos otro: 7 de 29 modos fallaban por el entorno, y la suite
dependía de un proyecto del usuario que no controla.

El contrato es simple y verificable sin levantar node: el proyecto viaja por
`HARNESS_PROJECT` (lo crea `suite.mjs` o lo elige `--project`), y el repo bajo
prueba se deriva del layout del plugin.
"""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
PLUGIN = HERE / "agent-plugins" / "walker-flight-control"
HARNESS = (PLUGIN / "gate.test.mjs", PLUGIN / "suite.mjs")


def test_el_harness_no_nombra_un_proyecto_del_usuario():
    for path in HARNESS:
        text = path.read_text()
        assert "game_reset" not in text, (
            f"{path.name} nombra un proyecto del usuario: el proyecto debe "
            "llegar por HARNESS_PROJECT / --project"
        )


def test_el_harness_no_hardcodea_rutas_absolutas():
    for path in HARNESS:
        text = path.read_text()
        assert "/Users/" not in text, (
            f"{path.name} tiene una ruta absoluta: derivala del layout "
            "(import.meta.url → ../..)"
        )


def test_el_proyecto_entra_por_entorno():
    gate = (PLUGIN / "gate.test.mjs").read_text()
    assert "HARNESS_PROJECT" in gate
    assert "HARNESS_PROJECT_DIR" in gate
    suite = (PLUGIN / "suite.mjs").read_text()
    assert "_/prepare" in suite, "sin --project el harness crea su fixture"
    assert "--project" in suite
