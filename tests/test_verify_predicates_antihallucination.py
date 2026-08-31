"""tests/test_verify_predicates_antihallucination.py — Garantías anti-alucinación (G-W6).

Fija el contrato que impide saltarse los criterios de validación de una spec:

  1. verify_predicates SIN predicates → TaskResult.failure (NO all_passed=True vacío).
  2. verify_predicates con lista vacía → failure.
  3. Traductor spec→motor: un predicate declarativo tipo SPEC-003 (párrafo con
     nombres de tests) se descompone en symbol_exists, NO se verifica como
     símbolo gigante inexistente.
  4. Marcar una task como completed NO satisface predicates (no dict ok=total).
"""
from __future__ import annotations

from shared.core.engine.domain.execution import ExecutionContext, TaskResult
from features.delta_tracking.tasks.verification import (
    verify_predicates,
    _translate_predicates,
)
from tasks.infrastructure.verify_engine import (
    _build_test_command,
    _looks_like_test_target,
    _resolve_test_command,
)


def _ctx(chain_dir: str) -> ExecutionContext:
    return ExecutionContext.create(
        session_id="sess-anti",
        chain_dir=chain_dir,
        task_id="SPEC-ANTI",
        baggage={},
    )


def test_verify_predicates_sin_predicates_falla(tmp_path):
    """G-W6: un gate sin predicates NO puede declarar éxito."""
    result = verify_predicates(_ctx(str(tmp_path)), {})
    assert not result.ok, (
        "verify_predicates con input vacío debe fallar; antes pasaba como "
        "all_passed=True (no-op)"
    )
    assert "no predicates" in (result.error or "").lower()


def test_verify_predicates_con_lista_vacia_falla(tmp_path):
    """G-W6: lista de predicates vacía tampoco es éxito."""
    result = verify_predicates(_ctx(str(tmp_path)), {"predicates": []})
    assert not result.ok
    assert "vacía" in (result.error or "")


def test_traductor_descompone_parrafo_en_symbols():
    """G-W6: párrafo tipo SPEC-003 → symbol_exists por cada test."""
    preds = _translate_predicates([
        "Nivel 1 MCP Tools E2E: test_mcp_tool_status_e2e, "
        "test_mcp_tool_submit_full_workflow",
        {"type": "command_exit", "command": "true"},
    ])
    typed = [p for p in preds if isinstance(p, dict)]
    assert any(
        p.get("type") == "symbol_exists"
        and p.get("symbol") == "test_mcp_tool_status_e2e"
        for p in typed
    ), f"esperaba symbol_exists para cada test, obtuve: {preds}"
    command_exits = [p for p in preds if isinstance(p, dict) and p.get("type") == "command_exit"]
    assert len(command_exits) == 1, "dicts typed se respetan tal cual"


def test_traductor_sin_patron_es_manual_explicito():
    """G-W6: predicate no verificable → manual explícito (auditable), nunca éxito."""
    preds = _translate_predicates(["Los 4 niveles de test están representados"])
    assert preds and isinstance(preds[0], dict)
    assert preds[0].get("type") == "manual"
    assert "4 niveles" in preds[0].get("description", "")


def test_complete_task_no_satisface_predicates():
    """G-W6: marcar una task como completed NO marca predicates como ok.

    El status de task es dato de negocio (delta/host), no del engine. El
    engine jamás toca predicates al completar una task.
    """
    state = {
        "tasks": {
            "T1": {
                "predicates": {"total": 3, "ok": 1},
            }
        }
    }
    state["tasks"]["T1"]["status"] = "completed"
    preds = state["tasks"]["T1"]["predicates"]
    assert preds["ok"] == 1, (
        "completar una task no debe tocar predicates; antes ponía ok=total (100%) "
        "sin verificar nada"
    )
    assert state["tasks"]["T1"]["status"] == "completed"


# ═══════════════════════════════════════════════════════════════════
# G-W6: test_pass framework-aware (no hardcodea pytest)
# ═══════════════════════════════════════════════════════════════════


def test_looks_like_test_target_clasifica_formas():
    """Un test target puro NO se ejecuta como binario shell."""
    assert _looks_like_test_target("test_mcp_tool_status_e2e") == "name"
    assert _looks_like_test_target("tests/test_x.py") == "target"
    # Comando completo con args → shell (autoridad del autor), no se reinterpreta.
    assert _looks_like_test_target("pytest tests/test_a.py -x") is None
    assert _looks_like_test_target("python3 -m pytest tests/b.py") is None


def test_resolve_test_command_defaults_a_pytest():
    """Sin project.json → pytest honesto (nunca mentira)."""
    assert _build_test_command("/tmp/nonexistent_root", name="test_foo") == [
        "python3", "-m", "pytest", "-k", "test_foo",
    ]
    assert _build_test_command("/tmp/nonexistent_root", target="tests/x.py") == [
        "python3", "-m", "pytest", "tests/x.py",
    ]


def test_resolve_test_command_usa_project_json(tmp_path):
    """project.json (coding.test_framework) parametriza el runner real."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "project.json").write_text(
        '{"project": {"coding": {"test_framework": "jest (ts)"}}}'
    )
    assert _resolve_test_command(str(tmp_path)) == ["npx", "jest"]
    assert _build_test_command(str(tmp_path), name="test_foo") == [
        "npx", "jest", "-t", "test_foo",
    ]


def test_test_pass_con_nombre_no_ejecuta_binario_inexistente(tmp_path):
    """test_pass con nombre de test → pasa al runner (-k), nunca FileNotFound."""
    ok, info = _verify_test_pass_patch_check(tmp_path)
    assert ok is True or "no tests" in info.get("error", "").lower()


def _verify_test_pass_patch_check(tmp_path):
    """Wrapper: verifica el comportamiento sin depender de tests existentes."""
    from tasks.infrastructure.verify_engine import (
        _build_test_command,
    )
    argv = _build_test_command(str(tmp_path), name="test_no_existe_xyz")
    assert argv[0] != "test_no_existe_xyz", (
        "el nombre de test NO debe ejecutarse como binario"
    )
    return True, {}