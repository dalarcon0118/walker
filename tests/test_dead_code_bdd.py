"""BDD: dead code real, sin los 1.014 falsos positivos del CPG.

Medido 2026-11-18: `bug_proposals.json` reportaba 1.014 `unused_functions` y
el review 955. Casi todos eran **callee stubs externos** (`server.add_tool`,
`Path`, `traceback.print_exc`) indexados como `type=function` +
`kind=external_call`; no son funciones del proyecto. Además,
`find_unused_functions` usaba `from` como "tiene callers" cuando el edge
CALLS va caller→callee: marcaba usados a los callers y muertas a las
funciones que sí tenían callers.

Después de ambos filtros quedan 22 funciones realmente sin referencias en el
repo. El gate baja de 50 a 25 (ratchet: baseline + 3 de margen).
"""
from __future__ import annotations

import os

import pytest

from fast_mcp.features.code_analysis.application.review_service import (
    MAX_DEAD_CODE_DEFAULT,
)
from fast_mcp.features.code_analysis.application.use_cases.review_code import (
    ReviewCodeUseCase,
)
from fast_mcp.features.code_analysis.domain.analysis import is_false_positive_dead_code
from fast_mcp.features.code_analysis.domain.services.analysis_service import (
    find_unused_functions,
)

ROOT = "/Users/davidmartinez/develop/Active-projects/walker"


def _nodes(**extra):
    base = {
        "caller": {
            "name": "caller",
            "type": "function",
            "kind": "",
            "path": "/x/a.py",
            "line": 1,
            "signature": "def caller()",
            "is_import": False,
        },
        "callee": {
            "name": "callee",
            "type": "function",
            "kind": "",
            "path": "/x/a.py",
            "line": 2,
            "signature": "def callee()",
            "is_import": False,
        },
    }
    base.update(extra)
    return base


def _run(nodes, edges):
    return find_unused_functions(
        nodes,
        edges,
        is_false_positive_dead_code=is_false_positive_dead_code,
        is_referenced=lambda *a, **k: False,
    )


def test_los_callee_stubs_externos_no_son_dead_code():
    assert is_false_positive_dead_code(
        "server.add_tool", "function", "", "/x/a.py", kind="external_call"
    )
    assert is_false_positive_dead_code(
        "Path", "function", "", "/x/a.py", kind="external_call"
    )
    assert not is_false_positive_dead_code(
        "funcion_real", "function", "def funcion_real()", "/x/a.py", kind=""
    )


def test_find_unused_functions_ignora_external_call():
    nodes = _nodes(
        ext={
            "name": "server.add_tool",
            "type": "function",
            "kind": "external_call",
            "path": "/x/a.py",
            "line": 3,
            "signature": "",
            "is_import": False,
        }
    )

    found = _run(nodes, [])

    names = {f["name"] for f in found}
    assert "server.add_tool" not in names


def test_el_callee_con_calls_incoming_esta_usado():
    # CALLS va caller -> callee: `callee` aparece como `to` y está usado.
    edges = [{"from": "caller", "to": "callee", "type": "CALLS"}]

    found = _run(_nodes(), edges)

    names = {f["name"] for f in found}
    assert "callee" not in names
    # `caller` no es callee de nadie: si no lo referencia el texto, es candidato.
    assert "caller" in names


def test_el_caller_con_calls_outgoing_no_oculta_al_callee():
    edges = [{"from": "caller", "to": "callee", "type": "CALLS"}]

    found = _run(_nodes(), edges)

    # Antes `incoming_ids` usaba `from`, así que `callee` (leaf) se reportaba
    # muerto aunque tuviera un caller.
    assert "callee" not in {f["name"] for f in found}


def test_el_gate_baja_a_25_y_pasa_con_el_baseline_real():
    assert MAX_DEAD_CODE_DEFAULT == 25

    report = ReviewCodeUseCase.execute(ROOT, files=[])
    gates = {g["name"]: g for g in report.get("gates") or []}

    assert gates["dead_code"]["threshold"] == 25
    assert gates["dead_code"]["actual"] <= 25
    assert report.get("passed") is True


def test_el_review_no_reporta_los_nombres_externos_conocidos():
    report = ReviewCodeUseCase.execute(ROOT, files=[])
    findings = (report.get("findings") or {}).get("dead_code") or []
    names = {f["function"] for f in findings}

    assert "server.add_tool" not in names
    assert "Path" not in names
    assert "traceback.print_exc" not in names
    assert "super().model_dump_one_level" not in names
