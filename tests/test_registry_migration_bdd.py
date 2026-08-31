"""test_registry_migration_bdd.py — Migración one-time del registro global.

El legacy resolvía su walker_root desde DENTRO de mcp/ (mcp/ tiene su propio
config/), así que el registro real vivía en mcp/data/projects.json. El server
autónomo (fuera de mcp/) usa el hogar final: <walker_root>/data/projects.json
(el ADR-071 §0 #5 elimina mcp/ al cierre, incluido su data/).

Este test es IDEMPOTENTE y actúa como guard de la transición: si el registro
final no existe y el legacy sí, lo migra (copia atómica simple). Cuando mcp/
se elimine, el guard solo verifica que el registro final existe.
"""

from __future__ import annotations

import json
import os
import shutil


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FINAL = os.path.join(_ROOT, "data", "projects.json")
_LEGACY = os.path.join(_ROOT, "mcp", "data", "projects.json")


def test_registro_global_en_su_hogar_final():
    if not os.path.exists(_FINAL) and os.path.exists(_LEGACY):
        # Transición: migrar el registro del legacy a su hogar final.
        os.makedirs(os.path.dirname(_FINAL), exist_ok=True)
        shutil.copyfile(_LEGACY, _FINAL)

    assert os.path.exists(_FINAL), (
        f"El registro global no existe en su hogar final: {_FINAL}"
    )
    with open(_FINAL) as f:
        reg = json.load(f)
    assert isinstance(reg.get("projects"), dict), "registro inválido"
