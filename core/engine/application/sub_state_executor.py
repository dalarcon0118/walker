"""application/sub_state_executor.py — Ejecución de sub-estados.

SRP: Responsabilidad única = ejecutar acciones de sub-estados y manejar
loops de auto-complete.
"""

from __future__ import annotations


from core.engine.domain.entities import WorkflowState
from core.engine.domain.ports import IWorkflowIO


def execute_sub_state_action(
    workflow: dict,
    node_id: str,
    sub_key: str,
    chain_dir: str | None,
    task_id: str | None,
    io: IWorkflowIO | None = None,
) -> dict:
    """Ejecuta auto_complete para un sub-estado mode:auto.

    Retorna dict con executed, exit_code, stdout, stderr.
    """
    if io is None:
        from core.engine.infrastructure.adapters import DefaultWorkflowIO

        io = DefaultWorkflowIO()

    ss = io.get_sub_state(workflow, node_id, sub_key)
    if not ss:
        return {
            "executed": False,
            "exit_code": 0,
            "stdout": "",
            "stderr": "Sub-state not found.",
        }

    mode = ss.get("mode", "manual")
    if mode != "auto":
        return {
            "executed": False,
            "exit_code": 0,
            "stdout": "Mode manual, no auto action.",
            "stderr": "",
        }

    script = ss.get("auto_complete", "")
    if not script:
        return {
            "executed": False,
            "exit_code": 0,
            "stdout": "No auto_complete script.",
            "stderr": "",
        }

    result = io.run_script(script, chain_dir, task_id)
    from string import Template

    print(Template("  [auto_complete] $action").substitute(action=script))
    if result["stdout"]:
        for line in result["stdout"].split("\n"):
            print(Template("    | $line").substitute(line=line))
    if result["stderr"]:
        for line in result["stderr"].split("\n"):
            io.eprint(Template("    ! $line").substitute(line=line))
    print()

    return {
        "executed": True,
        "exit_code": result["exit_code"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }


def handle_auto_loop(
    state: WorkflowState | dict,
    workflow: dict,
    chain_dir: str | None,
    current: str,
    failed_ss: dict,
    exec_result: dict,
    task_id: str | None = None,
) -> bool:
    """Si sub-estado tiene on_failure_loop, retrocede y persiste.

    Returns True si se manejó el loop, False si no aplica.
    """
    if not failed_ss.get("on_failure_loop") or not chain_dir:
        return False

    if isinstance(state, dict):
        sprint_state = WorkflowState.from_dict(state)
    else:
        sprint_state = state

    prev_idx = sprint_state.sub_state_index - 1
    if prev_idx < 0:
        return False

    node = workflow.get("nodes", {}).get(current, {})
    sub_states = node.get("sub_states", [])
    if prev_idx >= len(sub_states):
        return False

    prev_ss = sub_states[prev_idx]
    sprint_state = sprint_state.with_node(current, prev_ss["key"], prev_idx)
    from core.engine.engine import save_state

    save_state(sprint_state, chain_dir)

    from string import Template

    print(
        Template(
            "  [auto-loop] Retrocediendo a [$current.$prev_key] para correccion."
        ).substitute(current=current, prev_key=prev_ss["key"])
    )
    reason = (
        exec_result.get("stderr", "").strip()
        or exec_result.get("stdout", "").strip()[:200]
    )
    if reason:
        print(Template("  [auto-loop] Motivo: $reason").substitute(reason=reason[:200]))
    print()
    return True
