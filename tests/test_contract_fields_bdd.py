"""test_contract_fields_bdd.py — el contrato de un paso dice lo que la tarea hace.

Medido 2026-11-27 (auditoría campos↔tareas↔contratos): el `output_schema` de un
sub-estado declaraba campos que ningún handler devuelve, y el gate de aceptación
del PO afirmaba "ALL PASSED" escrito a mano. El agente y el PO leen ese contrato:
si miente, la decisión se toma sobre una promesa.

Contrato:
1. `PREDICATE_CHECK` declara `all_satisfied` (lo que devuelve verify.py), no
   `all_passed`.
2. `QA_RUN` declara los campos reales del runner (`returncode`, `passed`,
   `total`, `failures`), no `exit_code/stdout/stderr`.
3. Ningún output declara `status` (fantasma en 5 sub-estados).
4. `info_acquisition` no certifica adquisición sin queries ni gaps cerrados.
5. El briefing de PO_ACCEPT no afirma un verde ni usa `<exit_code>` literal.
6. Los `command_hint` no nombran tools retiradas.
"""

from __future__ import annotations

import glob
import json
import pathlib
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
NODES = ROOT / "data" / "workflow" / "nodes"
TEMPLATES = ROOT / "fast_mcp/features/workflow/application/prompts/templates"


def _sub(node: str, key: str) -> dict:
    data = json.loads((NODES / f"{node}.node.json").read_text())
    return next(s for s in data["sub_states"] if s["key"] == key)


def test_predicate_check_declara_el_campo_que_verify_devuelve():
    out = _sub("verify", "PREDICATE_CHECK")["output_schema"]

    assert "all_satisfied" in out["properties"]
    assert "all_satisfied" in out["required"]
    assert "all_passed" not in out["properties"]


def test_qa_run_declara_los_campos_del_runner():
    out = _sub("verify", "QA_RUN")["output_schema"]

    assert {"returncode", "passed", "total", "failures"} <= set(out["properties"])
    assert not {"exit_code", "stdout", "stderr"} & set(out["properties"])


#: Pasos con action cuyo handler SÍ devuelve `status` (verificado en el código):
#: spec_task (sprint_planning.py:424) y close_sprint (:466).
_HANDLERS_CON_STATUS = {"TASK_SPEC", "CLOSE"}


def test_ningun_output_con_action_declara_un_status_fantasma():
    """En un paso con `action`, el output lo produce el handler: `status` no
    existe salvo que el handler lo devuelva. En los pasos sin action el output
    lo produce el agente y `status` es legítimo."""
    con_status = []
    for path in glob.glob(str(NODES / "*.node.json")):
        for sub in json.loads(pathlib.Path(path).read_text())["sub_states"]:
            if not sub.get("action"):
                continue
            props = (sub.get("output_schema") or {}).get("properties") or {}
            if "status" in props:
                con_status.append(f"{sub['key']} ({sub['action']})")

    sobran = [item for item in con_status if item.split(" ")[0] not in _HANDLERS_CON_STATUS]
    assert not sobran, f"output declara `status` que ningún handler devuelve: {sobran}"


def test_info_acquisition_no_certifica_sin_evidencia():
    from fast_mcp.features.tasks.handlers.info_acquisition import info_acquisition

    ctx = SimpleNamespace(task_id="T-1", chain_dir="/tmp/p", session_id="s")

    vacio = info_acquisition(ctx, {}).data
    assert vacio["acquired"] is False
    assert "no certifica" in vacio["reason"]

    con_datos = info_acquisition(ctx, {"queries": ["q"]}).data
    assert con_datos["acquired"] is True


def test_po_accept_no_afirma_un_verde_ni_deja_placeholders_literales():
    tpl = json.loads((TEMPLATES / "nodes/verify/po-accept.prompt.json").read_text())["template"]

    assert "<exit_code>" not in tpl
    assert "{step_predicates}" in tpl, "el veredicto tiene que venir como dato, no como tarea"
    assert "sin verificar" in tpl, "sin dato se declara, no se asume"
    assert "Nunca escribas PASS" in tpl


def test_los_command_hint_no_nombran_tools_retiradas():
    retiradas = ("walker_analyze_code", "walker_cpg_business", "walker_recall_knowledge")
    hints = []
    for path in glob.glob(str(NODES / "*.node.json")):
        for sub in json.loads(pathlib.Path(path).read_text())["sub_states"]:
            for tool in sub.get("required_tools") or []:
                hints.append(str(tool.get("command_hint", "")))

    for hint in hints:
        for vieja in retiradas:
            assert vieja not in hint, f"command_hint nombra tool retirada: {vieja} en {hint!r}"


def test_el_veredicto_de_predicados_tiene_proveedor_siempre():
    """El PO no tiene que pedirlo: el dato viaja al prompt, y sin dato se declara."""
    from fast_mcp.features.workflow.application.prompt_context import predicate_verdict

    con_dato = SimpleNamespace(node_executions=[
        SimpleNamespace(task_result={"data": {"all_satisfied": False}}),
    ])
    assert predicate_verdict(con_dato) == "false"

    vacio = SimpleNamespace(node_executions=[SimpleNamespace(task_result={"data": {}})])
    assert predicate_verdict(vacio) == "sin verificar"
