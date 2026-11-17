"""test_baseline_snapshot_bdd.py — punto A y diff por scope.

El baseline no es "pasaron los tests": es el estado del sistema en las
dimensiones que el CPG conoce. Estas pruebas fijan el contrato del diff:
detectar cambios reales, no inventarlos por claves ambiguas, y marcar lo que no
se puede computar en vez de devolver ceros.
"""

from __future__ import annotations

from fast_mcp.features.code_analysis.application.baseline_service import (
    BaselineService,
    _new_hotspots,
)

BASE = {
    "label": "base",
    "taken_at": "t0",
    "scope": "harness/",
    "structure": {"nodes": 168, "files": 10, "functions": 114, "classes": 1, "signatures": 87},
    "relations": {"calls": 436, "imports": 37},
    "complexity": {"avg": 6.8, "max": 80, "above_10": 9, "top": [
        {"name": "main", "file": "harness/runner.py", "cyclo": 80},
        {"name": "main", "file": "harness/selftest.py", "cyclo": 18},
    ]},
    "files": {"count": 10, "digest": "aaa", "entries": {"harness/runner.py": "1:1"}},
    "conventions": {"status": "unavailable", "reason": "sin CKG"},
    "defects": {"status": "partial", "dead_code": 0, "taint_paths": 3},
}


def _now(**over):
    import copy

    now = copy.deepcopy(BASE)
    for path, value in over.items():
        node = now
        keys = path.split(".")
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = value
    return now


def test_sin_cambios_lo_dice():
    assert BaselineService._compare(BASE, _now())["summary"] == "sin cambios"


def test_detecta_archivo_agregado():
    now = _now(**{"files.entries": {"harness/runner.py": "1:1", "harness/nuevo.py": "new"}})

    changes = BaselineService._compare(BASE, now)["changes"]

    assert changes["files"]["added"] == ["harness/nuevo.py"]
    assert changes["files"]["counts"]["added"] == 1


def test_detecta_archivo_modificado_por_size_mtime():
    now = _now(**{"files.entries": {"harness/runner.py": "2:2"}})

    changes = BaselineService._compare(BASE, now)["changes"]

    assert changes["files"]["modified"] == ["harness/runner.py"]


def test_detecta_delta_estructural():
    now = _now(**{"structure.functions": 120})

    changes = BaselineService._compare(BASE, now)["changes"]

    assert changes["structure"]["functions"] == {"before": 114, "after": 120}


def test_complejidad_no_disponible_no_se_compara_como_numero():
    now = _now(**{"complexity": {"status": "unavailable", "reason": "sin cyclo"}})

    changes = BaselineService._compare(BASE, now)["changes"]

    assert changes["complexity"] == {"status": "unavailable"}


def test_hotspots_se_comparan_por_nombre_y_archivo():
    """Dos funciones `main` distintas no deben generar un salto falso."""
    base_cx = BASE["complexity"]
    now_cx = dict(base_cx, top=[
        {"name": "main", "file": "harness/runner.py", "cyclo": 80},
        {"name": "main", "file": "harness/selftest.py", "cyclo": 18},
        {"name": "main", "file": "harness/otro.py", "cyclo": 25},
    ])

    assert _new_hotspots(base_cx, now_cx) == [
        {"name": "main", "file": "harness/otro.py", "before": None, "after": 25}
    ]


def test_hotspot_que_empeora_se_reporta():
    base_cx = {"top": [{"name": "f", "file": "a.py", "cyclo": 12}]}
    now_cx = {"top": [{"name": "f", "file": "a.py", "cyclo": 20}]}

    assert _new_hotspots(base_cx, now_cx) == [
        {"name": "f", "file": "a.py", "before": 12, "after": 20}
    ]


def test_cli_rechaza_un_proyecto_no_registrado():
    """Un typo no puede caer al repo de walker y escribir ahí."""
    from fast_mcp import cli

    assert cli.run_task(["proyecto-que-no-existe/snapshot", "{}"]) == 2
