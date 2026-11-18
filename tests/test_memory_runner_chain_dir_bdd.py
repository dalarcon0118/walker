"""BDD: el runner de tools de memoria acepta `chain_dir`.

Regresión medida (2026-11-18): las acciones del workflow pasan
`ExecutionContext.chain_dir` (el repo), pero `_resolve_chain_dir` solo aceptaba
`project_id`. TODA tool de memoria invocada desde una acción devolvía
"No project context" y, como `gather_context`/`documenting_knowledge` son
fail-open, el CKG nunca se escribía y nadie veía el error.
"""
from __future__ import annotations

from fast_mcp.composition import _resolve_chain_dir, memory_tool_runner


def test_chain_dir_existente_se_usa_como_repo(tmp_path):
    assert _resolve_chain_dir({"chain_dir": str(tmp_path)}) == str(tmp_path)


def test_chain_dir_inexistente_no_se_inventa(tmp_path):
    assert _resolve_chain_dir({"chain_dir": str(tmp_path / "no-existe")}) == ""


def test_sin_chain_dir_ni_project_id_falla_explicito():
    assert _resolve_chain_dir({}) == ""


def test_runner_de_memoria_no_devuelve_no_project_context(tmp_path):
    # Tool de sólo lectura: mide la resolución del contexto, no escribe nada.
    result = memory_tool_runner("preflight")(chain_dir=str(tmp_path))

    assert isinstance(result, dict)
    assert "No project context" not in str(result.get("error", ""))
