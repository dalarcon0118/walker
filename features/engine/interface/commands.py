"""
commands — cmd status module

Extracted from walker.py.
"""

import os
import json
import sys
import subprocess
from string import Template
from datetime import datetime, timezone
from typing import Any
from libs.infrastructure.utils import (
    eprint,
    load_json,
    _parse_args,
    extract_chain_dir,
    _is_chain_dir,
    _list_project_sprints,
    _find_active_sprints,
    _load_tasks,
    _find_target,
    valid_transitions,
    _get_node_sub_states,
    _get_current_sub_state,
    _get_current_node,
    _get_current_sub_key,
    _get_required_fields,
    _get_sub_state,
    _get_sub_state_by_index,
    _report_invalid_gate,
    save_state,
    _resolve_task_id,
    _resolve_chain_id,
    _DONE_STATUSES,
    _SPRING_SCRIPT,
    _TASK_SCRIPT,
    _SCRIPT_DIR,
)
from .engine import (
    find_workflow,
    ensure_state,
    advance_sub_state,
    execute_sub_state_action,
    validate_gate,
)
from .plan_editor import (
    _execute_plan_edit,
    _load_backlog,
    _save_backlog,
    _mark_task_cancelled,
    _move_pending_to_backlog,
)


def _show_global_dashboard(sprints: list[dict]) -> None:
    """Print a global overview of all projects and their active sprints."""
    _show_section("DELTA WORKFLOW — GLOBAL DASHBOARD")

    if not sprints:
        print("  No se encontraron sprints activos.")
        print()
        print("  Para comenzar, necesitas un proyecto con workflow.json.")
        print("  Usa: walker.py status <project-directory>")
        print("  O crea un sprint desde INIT.")
        print()
        return

    # Group by project
    by_project: dict[str, list[dict]] = {}
    for s in sprints:
        by_project.setdefault(s["project_id"], []).append(s)

    for pid, project_sprints in sorted(by_project.items()):
        print(Template("  Proyecto: $pid").substitute(pid=pid))
        for s in project_sprints:
            walker = (
                Template(" [$ws]").substitute(ws=s["walker_state"])
                if s["walker_state"]
                else ""
            )
            print(
                Template("    $cid  status=$st  tasks=$tk$wlk").substitute(
                    cid=s["chain_id"].ljust(40),
                    st=s["status"].ljust(10),
                    tk=str(s["tasks"]),
                    wlk=walker,
                )
            )
        print()

    print("  Para ver detalle de un sprint:")
    print(Template("    walker.py status $ex").substitute(ex=sprints[0]["chain_dir"]))
    print()
    print("  Para ver estado del workflow en un proyecto:")
    print("    walker.py status --project <project-id>")
    print()


def _load_task_context(chain_dir: str, task_id: str) -> dict | None:
    """Load task data from plan.json, return None if not found."""
    plan_path = os.path.join(chain_dir, "plan.json")
    if not os.path.exists(plan_path):
        return None
    pd = load_json(plan_path)
    if not pd:
        return None
    pl = pd.get("plan", pd)
    for t in pl.get("tasks", []):
        if t.get("task_id") == task_id:
            return t
    return None


def _show_task_info(chain_dir: str, task_id: str | None) -> dict | None:
    """Print current task details. Returns task data or None."""
    current_task = _load_task_context(chain_dir, task_id) if task_id else None
    if not current_task:
        return None
    print(
        Template("  Tarea: $tid  domain=$dom  type=$wt").substitute(
            tid=task_id,
            dom=current_task.get("domain", ""),
            wt=current_task.get("work_type", ""),
        )
    )
    print(
        Template("    $obj").substitute(obj=(current_task.get("objective", "")[:100]))
    )
    ai = current_task.get("impacted_artifacts", {})
    files = ai.get("files", []) if isinstance(ai, dict) else []
    if files:
        print(Template("  Archivos: $f").substitute(f=", ".join(files[:5])))
        if len(files) > 5:
            print(Template("    ... y $n mas").substitute(n=str(len(files) - 5)))
    print()
    return current_task


def _scan_related_tasks(
    chain_dir: str, task_id: str, current_task: dict, deep: bool
) -> dict:
    """Scan other sprints for tasks related by file, domain, or keywords."""
    task_files = set()
    ai = current_task.get("impacted_artifacts", {})
    if isinstance(ai, dict):
        task_files.update(ai.get("files", []))

    delta_dir = os.path.dirname(os.path.join(chain_dir))
    parent_delta = os.path.dirname(delta_dir)
    if not os.path.isdir(parent_delta):
        parent_delta = delta_dir

    result = {"file": set(), "domain": set(), "kw": set()}
    task_obj = (current_task.get("objective") or "").lower()
    task_domain = current_task.get("domain", "")
    task_type = current_task.get("work_type", "")
    task_keywords = set(task_obj.split()[:10]) if task_obj else set()

    for entry in sorted(os.listdir(parent_delta)):
        sprint_dir = os.path.join(parent_delta, entry)
        if not os.path.isdir(sprint_dir) or sprint_dir == chain_dir:
            continue
        other_plan = os.path.join(sprint_dir, "plan.json")
        if not os.path.exists(other_plan):
            continue
        try:
            with open(other_plan) as f:
                opd = json.load(f)
            opl = opd.get("plan", opd)
            sc = opl.get("meta", {}).get("chain_id", entry)
            for t in opl.get("tasks", []):
                tid = t.get("task_id", "")
                if tid == task_id:
                    continue
                oi = t.get("impacted_artifacts", {})
                if isinstance(oi, dict) and any(
                    f in task_files for f in oi.get("files", [])
                ):
                    result["file"].add((sc, tid))
                if deep:
                    if (
                        t.get("domain") == task_domain
                        and t.get("work_type") == task_type
                    ):
                        result["domain"].add((sc, tid))
                    obj_words = set((t.get("objective") or "").lower().split())
                    if len(task_keywords & obj_words) >= 2:
                        result["kw"].add((sc, tid))
        except (json.JSONDecodeError, OSError):
            continue
    return result


def _show_auto_context(
    chain_dir: str | None, task_id: str | None, deep: bool = False
) -> None:
    """Show automatic context: sprint info + task info + cross-sprint related work."""
    if not chain_dir:
        return
    _show_sprint_info(chain_dir)
    current_task = _show_task_info(chain_dir, task_id)
    if current_task and task_id:
        related = _scan_related_tasks(chain_dir, task_id, current_task, deep)
        _show_related_results(related, deep)


def cmd_status(sub_args: list[str]) -> int:
    """walker status: muestra estado + sub-estado + transiciones inter-node."""
    sub_args, chain_dir = extract_chain_dir(sub_args)
    deep = "--deep" in sub_args or "-d" in sub_args
    if deep:
        sub_args = [a for a in sub_args if a not in ("--deep", "-d")]

    if chain_dir and not _is_chain_dir(chain_dir):
        return _list_project_sprints(chain_dir)

    if not chain_dir:
        positional, flags = _parse_args(sub_args)
        project_flag = flags.get("project", None)
        if project_flag:
            sprints = _find_active_sprints()
            project_sprints = [s for s in sprints if s["project_id"] == project_flag]
            if not project_sprints:
                eprint(
                    Template(
                        "No se encontraron sprints activos para el proyecto '$pid'."
                    ).substitute(pid=project_flag)
                )
                return 1
            chain_dir = project_sprints[0]["chain_dir"]
        else:
            sprints = _find_active_sprints()
            _show_global_dashboard(sprints)
            return 0

    state = ensure_state(chain_dir)
    workflow = find_workflow(chain_dir)

    current = _get_current_node(state)
    node = workflow["nodes"].get(current, {})
    sub_state = _get_current_sub_state(workflow, state)
    sub_states = _get_node_sub_states(workflow, current)
    ss_index = state.get("sub_state_index", 0)

    _show_section("WALKER STATUS v4")
    if state.get("context", {}).get("reconstructed"):
        print(
            Template("  [auto-rebuild] Estado reconstruido desde $src").substitute(
                src=state["context"].get("reconstructed_from", ".workflow-state.json")
            )
        )
        print()
    _show_node_header(current, node, sub_state)
    _show_mode(node, sub_state)
    # Show sub-state progress
    if sub_states:
        done_count = min(ss_index, len(sub_states))
        print(
            Template("  Sub-state progress: $done/$total").substitute(
                done=str(done_count), total=str(len(sub_states))
            )
        )
        for i, ss in enumerate(sub_states):
            icon = "✅" if i < ss_index else "⬜" if i == ss_index else "⬜"
            mode_icon = "🤖" if ss.get("mode") == "auto" else "✋"
            print(
                Template("    $icon [$mode] $key").substitute(
                    icon=icon, mode=mode_icon, key=ss["key"]
                )
            )
        print()
    _show_context(state)
    _show_transitions(workflow, current, chain_dir)
    _show_history(state)
    _show_on_entry_message(
        node, chain_dir, state.get("context", {}).get("task_id"), sub_state
    )
    _show_auto_context(chain_dir, state.get("context", {}).get("task_id"), deep)
    _show_flow_progress(state)

    return 0


def cmd_current(sub_args: list[str]) -> int:
    """walker current: detalle del nodo y sub-estado actual."""
    sub_args, chain_dir = extract_chain_dir(sub_args)
    if chain_dir and not _is_chain_dir(chain_dir):
        return _list_project_sprints(chain_dir)
    state = ensure_state(chain_dir)
    workflow = find_workflow(chain_dir)

    current = _get_current_node(state)
    sub_key = _get_current_sub_key(state)
    node = workflow["nodes"].get(current, {})
    sub_state = _get_sub_state(workflow, current, sub_key)

    print(
        Template("Nodo actual: [$current.$sub_key]").substitute(
            current=current, sub_key=sub_key
        )
    )
    print(Template("  Label:       $label").substitute(label=node.get("label", "-")))
    print(Template("  Sub-state:   $key").substitute(key=sub_key))
    if sub_state:
        print(
            Template("  Mode:        $mode").substitute(
                mode=sub_state.get("mode", "manual")
            )
        )
        req = sub_state.get("required_fields", [])
        if req:
            print(Template("  Required:    $fields").substitute(fields=", ".join(req)))
    ctx = state.get("context", {})
    if ctx.get("chain_dir"):
        print(Template("  chain_dir: $d").substitute(d=ctx["chain_dir"]))
    if ctx.get("task_id"):
        print(Template("  task_id:   $t").substitute(t=ctx["task_id"]))

    return 0


def _resolve_implicit_task_id(state: dict, chain_dir: str) -> str | None:
    """Resuelve task_id implicito desde el contexto del state o el delta."""
    ctx_task = state.get("context", {}).get("task_id")
    if ctx_task:
        print(Template("  [task_id] Desde contexto: $tid").substitute(tid=ctx_task))
        return ctx_task
    rejected = set(state.get("context", {}).get("rejected_tasks", []))
    implicit = _resolve_task_id(chain_dir, rejected)
    if implicit:
        print(Template("  [task_id] Detectado: $tid").substitute(tid=implicit))
        return implicit
    return None


def _find_sub_state_gate(workflow: dict, current: str, gate: str) -> str | None:
    """Busca el sub_state_gate de la edge current->gate (si es intra-node)."""
    for e in workflow.get("edges", []):
        if e.get("from") == current and e.get("gate") == gate:
            return e.get("sub_state_gate")
    return None


def _collect_contract_data(
    workflow: dict, state: dict, current: str, flags: dict
) -> dict:
    """Recolecta los datos del contrato para la transicion."""
    current_ss = _get_current_sub_state(workflow, state)
    contract = None
    if current_ss:
        contract = current_ss.get("data_contract")
    if not contract:
        contract = workflow["nodes"].get(current, {}).get("data_contract")
    contract_data = {}
    if contract and isinstance(contract, dict):
        fields = contract.get("fields", [])
        contract_data = {k: flags[k] for k in fields if k in flags}
        if contract_data:
            for k, v in contract_data.items():
                print(
                    Template("  [data] $key: $val").substitute(key=k, val=str(v)[:60])
                )
    return contract_data


def _report_preconditions_blocked(pre_msg: str, workflow: dict, current: str) -> int:
    """Reporta una transicion bloqueada por precondiciones."""
    print()
    eprint("=" * 70)
    eprint("  TRANSITION BLOQUEADA: Precondiciones no cumplidas")
    eprint(pre_msg)
    eprint("=" * 70)
    print()
    return 1


def _report_gate_validation_failed(vresult: dict, workflow: dict, current: str) -> int:
    """Reporta una gate que fallo la validacion, con gates disponibles."""
    print()
    eprint("=" * 70)
    eprint(
        Template("  Gate validation FAILED: $msg").substitute(msg=vresult["message"])
    )
    eprint("=" * 70)
    trans = valid_transitions(workflow, current)
    eprint("  Gates disponibles desde este estado:")
    for t in trans:
        g = (
            Template("transition $gate").substitute(gate=t["gate"])
            if t.get("gate")
            else "(sin gate)"
        )
        eprint(
            Template("    walker.py $cmd  -> $desc").substitute(
                cmd=g, desc=t["gate_description"]
            )
        )
    eprint("=" * 70)
    print()
    return 1


def _update_task_status_on_gate(
    chain_dir: str | None,
    task_id: str | None,
    gate: str,
) -> None:
    """Actualiza el task status en el state consolidado segun la gate."""
    if not (chain_dir and task_id):
        return
    from features.engine.infrastructure.repositories import (
        WorkflowStateRepository as _SSR,
    )

    def load_state(c):
        return _SSR().load_dict(c) or {}

    def save_state(s, c):
        return _SSR().save_dict(c, s) if isinstance(s, dict) else _SSR().save(c, s)

    s = load_state(chain_dir)
    if gate == "task_complete":
        s.update({"tasks": {task_id: {"status": "completed"}}})
        save_state(s, chain_dir)
    elif gate == "analysis_approved":
        s.update({"tasks": {task_id: {"status": "in_progress"}}})
        save_state(s, chain_dir)


def _run_auto_complete_chain(
    workflow: dict,
    state: dict,
    target: str,
    chain_dir: str | None,
    task_id: str | None,
    is_self_loop: bool,
) -> None:
    """Ejecuta la cadena de auto-complete al entrar a un nodo nuevo."""
    if not is_self_loop:
        auto_ss = _get_sub_state_by_index(workflow, target, 0)
        while auto_ss and auto_ss.get("mode") == "auto":
            exec_result = execute_sub_state_action(
                workflow, target, auto_ss["key"], chain_dir, task_id
            )
            if exec_result["exit_code"] == 0:
                advance_sub_state(state, workflow)
                save_state(state, chain_dir)
                print(
                    Template(
                        "  [auto] Sub-state '$key' completado automaticamente."
                    ).substitute(key=auto_ss["key"])
                )
                auto_ss = _get_current_sub_state(workflow, state)
            else:
                eprint(
                    Template("  ⚠️  Auto-complete '$key' fallo (exit $code)").substitute(
                        key=auto_ss["key"], code=str(exec_result["exit_code"])
                    )
                )
                break
        current_ss = _get_current_sub_state(workflow, state)
        _show_on_entry_message(
            workflow["nodes"].get(target, {}), chain_dir, task_id, current_ss
        )
    else:
        _show_on_entry_message(
            workflow["nodes"].get(target, {}),
            chain_dir,
            task_id,
            _get_current_sub_state(workflow, state),
        )


def cmd_transition(sub_args: list[str]) -> int:
    """walker transition <gate>: inter-node transition (v4).

    Solo permite transiciones entre nodos (SPRINT/ANALYZE/IMPLEMENT/VERIFY/REFINE).
    Las transiciones internas se manejan via submit (TASK-002+).
    """
    if not sub_args:
        eprint("Uso: walker.py transition <gate> [<chain_dir>] [--params ...]")
        return 2

    gate = sub_args[0]
    if gate.lower() in ("null", "none"):
        gate = None
    rest_args = sub_args[1:]
    chain_dir = None
    task_id = None

    rest_args, chain_dir = extract_chain_dir(rest_args)

    positional, flags = _parse_args(rest_args)

    # Resolve sprint ID from positionals if chain_dir not extracted
    if not chain_dir:
        for p in positional:
            if os.path.isdir(p) and _is_chain_dir(p):
                chain_dir = p
                positional.remove(p)
                break
            resolved = _resolve_chain_id(p)
            if resolved:
                chain_dir = resolved
                positional.remove(p)
                break

    reason = flags.get("reason", "")
    task_id = flags.get("task_id", None)

    state = ensure_state(chain_dir)
    workflow = find_workflow(chain_dir)
    current = _get_current_node(state)
    sub_key = _get_current_sub_key(state)

    # Implicit task_id from state context or el delta (nodo spring)
    if not task_id and chain_dir:
        task_id = _resolve_implicit_task_id(state, chain_dir)

    # 1. Find target node via inter-node edge
    target = _find_target(workflow, current, gate)
    if not target:
        return _report_invalid_gate(workflow, current, gate)

    # Check if self-loop (intra-node gate like edit_plan, close_sprint)
    is_self_loop = target == current
    sub_state_gate = _find_sub_state_gate(workflow, current, gate)

    # 2. Collect contract data from sub_state (v4) or node (legacy)
    contract_data = _collect_contract_data(workflow, state, current, flags)

    # 2a. Persist contract data
    if contract_data:
        _store_contract_data(chain_dir, task_id, target, contract_data)

    # 2b. Validate preconditions for inter-node transitions
    if not is_self_loop:
        pre_ok, pre_msg = _check_transition_preconditions(
            state, workflow, current, gate, chain_dir
        )
        if not pre_ok:
            return _report_preconditions_blocked(pre_msg, workflow, current)

    # 2c. Validate gate (using node gate_validation)
    vresult = validate_gate(workflow, current, gate, chain_dir, task_id)
    if not vresult["ok"]:
        return _report_gate_validation_failed(vresult, workflow, current)

    # 3. Execute plan action BEFORE transition
    if (
        current == "SPRINT"
        and sub_key == "EDIT"
        and gate in ("plan_updated", "edit_plan")
    ):
        ok = _execute_plan_edit(chain_dir, task_id, flags)
        if not ok:
            print()
            eprint("=" * 70)
            eprint(
                "  [edit_plan] La operacion fallo. Revisa los errores e intenta de nuevo."
            )
            eprint("=" * 70)
            print()
            return 1

    # 4. Execute state transition
    old_node = current
    if is_self_loop and sub_state_gate:
        # Intra-node: jump to specific sub-state
        new_ss = _get_sub_state(workflow, current, sub_state_gate)
        if new_ss:
            # Find index of target sub_state
            sub_states = _get_node_sub_states(workflow, current)
            new_idx = next(
                (i for i, ss in enumerate(sub_states) if ss["key"] == sub_state_gate), 0
            )
            state["current_sub_state"] = sub_state_gate
            state["sub_state_index"] = new_idx
            state["history"].append(f"{current}.{sub_state_gate}")
            print(
                Template("[$old] -> [$current.$sub] (gate: $gate)").substitute(
                    old=old_node, current=current, sub=sub_state_gate, gate=gate
                )
            )
        else:
            _apply_state_transition(
                state, target, old_node, gate, reason, chain_dir, task_id
            )
    else:
        _apply_state_transition(
            state, target, old_node, gate, reason, chain_dir, task_id
        )

    # 4b-4e. Side effects post-transicion (pending, cancel, decisions, status)
    _apply_transition_side_effects(
        current, sub_key, gate, chain_dir, task_id, reason
    )

    # 5. If moving to new node, trigger auto_complete chain on first sub-states
    _run_auto_complete_chain(
        workflow, state, target, chain_dir, task_id, is_self_loop
    )

    return 0


def _apply_transition_side_effects(
    current: str,
    sub_key: str,
    gate: str,
    chain_dir: str,
    task_id: str,
    reason: str,
) -> None:
    """Aplica side-effects post-transicion: pending, cancel, decisions, status."""
    # 4b. Handle HANDLE_PENDING actions
    if current == "SPRINT" and sub_key == "HANDLE_PENDING" and chain_dir:
        if gate == "backlog":
            _move_pending_to_backlog(chain_dir)
        elif gate == "cancel_pending":
            _move_pending_to_backlog(chain_dir)

    # 4c. Handle cancel (from IMPLEMENT)
    if gate == "cancel" and chain_dir and task_id:
        _mark_task_cancelled(chain_dir, task_id, reason)

    # 4d. Log decisions for key gates
    decision_gates = {
        "proceed",
        "refine",
        "cancel",
        "needs_refine",
        "analysis_approved",
        "implementation_done",
        "task_complete",
        "reroute_analyze",
        "reroute_implement",
        "reroute_verify",
        "reroute_sprint",
        "start_task",
        "close_sprint",
    }
    if gate in decision_gates and chain_dir:
        _log_decision(chain_dir, f"{current}.{sub_key}", gate, task_id, reason)

    # 4e. Actualizar task status en consolidated state
    _update_task_status_on_gate(chain_dir, task_id, gate)


def cmd_resume(sub_args: list[str]) -> int:
    """walker resume <node>: salta a un nodo validando modelo completo.

    A diferencia de goto (emergencia), resume verifica que todos los
    sub-estados del nodo origen hasta el destino tengan sus required_fields
    completados. Si falta algun campo, muestra la lista exacta de
    sub-estados incompletos.

    Uso: walker.py resume <node> [<chain_dir>] [--reason '...']
    """
    if not sub_args:
        eprint("Uso: walker.py resume <nodo> [<chain_dir>] [--reason '...']")
        eprint("  Nodos: SPRINT, ANALYZE, IMPLEMENT, VERIFY, REFINE")
        return 2

    target = sub_args[0]
    rest_args = sub_args[1:]
    chain_dir = None
    if rest_args and not rest_args[0].startswith("--"):
        chain_dir = os.path.abspath(rest_args[0])
        rest_args = rest_args[1:]

    if chain_dir and not _is_chain_dir(chain_dir):
        return _list_project_sprints(chain_dir)

    positional, flags = _parse_args(rest_args)
    reason = flags.get("reason", "Resume manual")

    state = ensure_state(chain_dir)
    workflow = find_workflow(chain_dir)

    if target not in workflow["nodes"]:
        eprint(Template("Nodo desconocido: $target").substitute(target=target))
        eprint(
            Template("  Nodos validos: $nodes").substitute(
                nodes=", ".join(workflow["nodes"].keys())
            )
        )
        return 2

    current = _get_current_node(state)

    if current == target:
        eprint(
            Template(
                "Ya estas en [$current]. submit para navegar dentro del nodo."
            ).substitute(current=current)
        )
        eprint(
            "  resume es para saltar a OTRO nodo. Usa submit para avanzar sub-estados."
        )
        return 1

    # Validate model: check all completed sub_states for current node
    # and verify that required_fields are filled in sub_state_data
    sub_states = _get_node_sub_states(workflow, current)
    incomplete = []
    for ss in sub_states:
        ss_key = ss.get("key", "")
        req = ss.get("required_fields", [])
        if not req:
            continue
        data_key = f"{current}.{ss_key}"
        stored = state.get("sub_state_data", {}).get(data_key, {})
        missing = [f for f in req if f not in stored]
        if missing:
            incomplete.append(
                Template("  [$current.$key] campos faltantes: $fields").substitute(
                    current=current, key=ss_key, fields=", ".join(missing)
                )
            )

    if incomplete:
        eprint("=" * 70)
        eprint(Template("  RESUME BLOQUEADO para [$target]").substitute(target=target))
        eprint("  Sub-estados con datos incompletos en el nodo actual:")
        for msg in incomplete:
            eprint(msg)
        eprint()
        eprint("  Completa los datos con submit, o usa goto para saltar validacion.")
        eprint("=" * 70)
        return 1

    # All validations passed — execute resume
    old_node = current
    old_sub = _get_current_sub_key(state)
    target_node = workflow.get("nodes", {}).get(target, {})
    sub_states = target_node.get("sub_states", [])
    first_key = sub_states[0]["key"] if sub_states else "INIT"

    state["current_node"] = target
    state["current_sub_state"] = first_key
    state["sub_state_index"] = 0
    state["history"].append(f"RESUME->{target}.{first_key}")
    if chain_dir:
        state["context"]["chain_dir"] = chain_dir
    save_state(state, chain_dir)

    print(
        Template("Resume: [$old.$sub] -> [$target.$first]").substitute(
            old=old_node, sub=old_sub, target=target, first=first_key
        )
    )
    if reason:
        print(Template("  Razon: $reason").substitute(reason=reason))
    print("  (resume es un salto validado. Para emergencia usa goto).")
    print()

    # Trigger auto-complete on first sub-state if auto
    first_ss = sub_states[0] if sub_states else None
    if first_ss and first_ss.get("mode") == "auto":
        exec_result = execute_sub_state_action(
            workflow, target, first_ss["key"], chain_dir, task_id
        )
        if exec_result["exit_code"] == 0:
            advance_sub_state(state, workflow)
            save_state(state, chain_dir)
            print(
                Template("  [auto] Sub-state '$key' completado.").substitute(
                    key=first_ss["key"]
                )
            )
        else:
            eprint(
                Template("  ⚠️  Auto-complete '$key' fallo (exit $code)").substitute(
                    key=first_ss["key"], code=str(exec_result["exit_code"])
                )
            )

    _show_on_entry_message(
        workflow["nodes"].get(target, {}),
        chain_dir,
        None,
        _get_current_sub_state(workflow, state),
    )
    return 0


def cmd_goto(sub_args: list[str]) -> int:
    """walker goto <state>: fuerza transicion (recovery, sin validacion)."""
    if not sub_args:
        eprint("Uso: walker.py goto <estado> [<chain_dir>] [--reason '...']")
        return 2

    target = sub_args[0]
    rest_args = sub_args[1:]
    chain_dir = None
    if rest_args and not rest_args[0].startswith("--"):
        chain_dir = os.path.abspath(rest_args[0])
        rest_args = rest_args[1:]

    if chain_dir and not _is_chain_dir(chain_dir):
        return _list_project_sprints(chain_dir)

    positional, flags = _parse_args(rest_args)
    reason = flags.get("reason", "Forzado manualmente")

    state = ensure_state(chain_dir)
    workflow = find_workflow(chain_dir)

    if target not in workflow["nodes"]:
        eprint(Template("Estado desconocido: $target").substitute(target=target))
        eprint(
            Template("  Estados validos: $nodes").substitute(
                nodes=", ".join(workflow["nodes"].keys())
            )
        )
        return 2

    # Warn about skipped flow steps
    _FLOW_STEPS = [
        ("IN_PROGRESS", "Investigacion de contexto"),
        ("ANALYSIS_GATE", "Analisis 6→3 capas"),
        ("ANALYSIS_REVIEW", "Revision de analisis"),
        ("USER_GATE", "Gate de usuario (pre-implementacion)"),
        ("ADVANCE_TASK", "Implementacion"),
        ("DONE_REVIEW", "Revision de implementacion"),
        ("CODE_REVIEW", "Code review"),
        ("REPORT_GATE", "Gate de usuario (post-implementacion)"),
        ("VERIFY", "QA verification"),
        ("PO_ACCEPTANCE", "PO acceptance"),
        ("CLOSE_CHECK", "Cierre de sprint"),
    ]
    history_states = set(state.get("history", []))
    skipped = [
        label
        for node, label in _FLOW_STEPS
        if node not in history_states and node != target
    ]
    if skipped:
        print()
        print("  ⚠️  GOTO: Te estas saltando:")
        for s in skipped:
            print(Template("    - $label").substitute(label=s))
        print("  El workflow no tendra trazabilidad completa en estos pasos.")
        print("  Para seguir el flujo recomendado, usa transition en vez de goto.")
        print()

    old_node = _get_current_node(state)
    # Resolve first sub-state key from workflow
    workflow = find_workflow(chain_dir)
    target_node = workflow.get("nodes", {}).get(target, {})
    sub_states = target_node.get("sub_states", [])
    first_key = sub_states[0]["key"] if sub_states else "INIT"
    state["current_node"] = target
    state["current_sub_state"] = first_key
    state["sub_state_index"] = 0
    state["history"] = state["history"] + [f"!FORCE->{target}.{first_key}"]
    if chain_dir:
        state["context"]["chain_dir"] = chain_dir
    save_state(state, chain_dir)

    print(
        Template("FORCE: [$old] -> [$target.$first] (v4)").substitute(
            old=old_node, target=target, first=first_key
        )
    )
    print(Template("  Razon: $reason").substitute(reason=reason))
    print(
        "  ⚠️  goto es un comando de emergencia. Para flujo normal usa transition o submit."
    )
    print()
    return 0


def cmd_reset(sub_args: list[str]) -> int:
    """walker reset: vuelve a SPRINT.INIT."""
    rest_args, chain_dir = extract_chain_dir(sub_args)
    if chain_dir and not _is_chain_dir(chain_dir):
        return _list_project_sprints(chain_dir)
    positional, flags = _parse_args(rest_args)
    reason = flags.get("reason", "Reset solicitado")

    state = ensure_state(chain_dir)
    old_node = _get_current_node(state)
    old_sub = _get_current_sub_key(state)
    state["current_node"] = "SPRINT"
    state["current_sub_state"] = "INIT"
    state["sub_state_index"] = 0
    state["history"] = ["SPRINT.INIT"]
    state["sub_state_data"] = {}
    state["context"] = {"chain_dir": chain_dir, "task_id": None}
    save_state(state, chain_dir)

    print(
        Template("Reset: [$old.$sub] -> [SPRINT.INIT]").substitute(
            old=old_node, sub=old_sub
        )
    )
    if chain_dir:
        print(Template("  Sprint: $d").substitute(d=chain_dir))
    print(Template("  Razon: $reason").substitute(reason=reason))
    print("  Historial limpiado. Workflow reset a SPRINT.INIT (v4).")
    return 0


def _query_task(
    tid: str,
    tasks: list[dict],
    flags: dict,
    has_files_flag: bool,
    chain_dir: str | None = None,
) -> int:
    """Query: show details of a specific task, including status from the delta."""
    task = next((t for t in tasks if t.get("task_id") == tid), None)
    if not task:
        eprint(Template("Tarea no encontrada: $tid").substitute(tid=tid))
        return 1

    # Read task status via el DELTA (nodo spec — fuente unica)
    status = "unknown"
    if chain_dir:
        try:
            from features.delta_tracking.application.services.spring_service import (
                SpringService,
            )

            spring_svc = SpringService.from_chain_dir(chain_dir)
            if spring_svc:
                spring_id = spring_svc.get_spring_id(chain_dir)
                spring_svc.ensure_spring_imported(chain_dir, spring_id)
                spec = spring_svc.get_spec(spring_id, tid)
                if spec:
                    status = spec.get("status", "unknown")
        except Exception:
            pass

    print(Template("Tarea: $tid  Status: $st").substitute(tid=tid, st=status))
    print(Template("  Objective: $obj").substitute(obj=task.get("objective", "-")))
    print(Template("  Result state: $rs").substitute(rs=task.get("result_state", "-")))
    print(
        Template("  Domain: $d  Type: $t").substitute(
            d=task.get("domain", "-"), t=task.get("work_type", "-")
        )
    )
    ai = task.get("impacted_artifacts", {})
    files = ai.get("files", []) if isinstance(ai, dict) else []
    if files and (has_files_flag or not flags):
        print(Template("  Files: $f").substitute(f=", ".join(files)))
    if has_files_flag or not flags:
        tc = task.get("task_context", {})
        if isinstance(tc, dict):
            for k, v in tc.items():
                if isinstance(v, str) and len(v) > 50:
                    print(Template("  $key: $val...").substitute(key=k, val=v[:80]))
    return 0


def _query_file(qarg: str, tasks: list[dict]) -> int:
    """Query: find tasks that impact a file."""
    matching = [
        t.get("task_id", "?")
        for t in tasks
        if any(
            qarg in f
            for f in (
                t.get("impacted_artifacts", {}).get("files", [])
                if isinstance(t.get("impacted_artifacts", {}), dict)
                else []
            )
        )
    ]
    if matching:
        print(Template("Archivo '$f' impactado por:").substitute(f=qarg))
        for tid in matching:
            print(Template("  $tid").substitute(tid=tid))
    else:
        print(
            Template("No se encontraron tareas que impacten '$f'.").substitute(f=qarg)
        )
    return 0


def _query_status(chain_dir: str, tasks: list[dict]) -> int:
    """Query: show status summary of all tasks in the sprint.

    PRINCIPIO: el estado de negocio del spring vive en el DELTA.
    """
    from features.delta_tracking.application.services.spring_service import (
        SpringService,
    )

    task_states = {}
    try:
        spring_svc = SpringService.from_chain_dir(chain_dir)
        if spring_svc:
            spring_id = spring_svc.get_spring_id(chain_dir)
            spring_svc.ensure_spring_imported(chain_dir, spring_id)
            task_states = {
                s.get("spec_id", s.get("task_id", "")): s.get("status", "")
                for s in spring_svc.get_specs(spring_id)
            }
    except Exception:
        pass

    by_status: dict[str, list[str]] = {}
    for t in tasks:
        tid = t.get("task_id", "")
        st = task_states.get(tid, "unknown")
        by_status.setdefault(st, []).append(tid)

    print("  ── TAREAS ──")
    for st in [
        "pending",
        "in_progress",
        "completed",
        "achieved",
        "needs_revision",
        "unknown",
    ]:
        ids = by_status.pop(st, [])
        if ids:
            icon = {
                "pending": "⬜",
                "in_progress": "🔄",
                "completed": "✅",
                "achieved": "✅",
                "needs_revision": "⚠️",
                "unknown": "❓",
            }.get(st, "⬜")
            print(
                Template("  $icon $st ($n):").substitute(
                    icon=icon, st=st, n=str(len(ids))
                )
            )
            for tid in ids[:10]:
                print(Template("    $tid").substitute(tid=tid))
            if len(ids) > 10:
                print(Template("    ... y $n mas").substitute(n=str(len(ids) - 10)))
    for st, ids in sorted(by_status.items()):
        print(Template("  $st ($n):").substitute(st=st, n=str(len(ids))))
        for tid in ids[:5]:
            print(Template("    $tid").substitute(tid=tid))

    completed = sum(
        1
        for s in task_states.values()
        if isinstance(s, str) and s.lower() in _DONE_STATUSES
    )
    total = len(task_states)
    print(
        Template("  Total: $done/$total completadas").substitute(
            done=str(completed), total=str(total)
        )
    )
    return 0


def _query_blocked(tasks: list[dict]) -> int:
    """Query: show tasks with potential blockers (needs_revision, high risks)."""
    blocked = []
    for t in tasks:
        tid = t.get("task_id", "")
        risks = t.get("risks", {})
        curvature = risks.get("curvature", 0) if isinstance(risks, dict) else 0
        if curvature > 0.7:
            blocked.append((tid, f"riesgo alto (curvature={curvature})"))
        preconditions = t.get("preconditions", [])
        if preconditions:
            blocked.append((tid, f"precondiciones: {', '.join(preconditions[:3])}"))

    if blocked:
        print("  ── POSIBLES BLOQUEOS ──")
        for tid, reason in blocked[:10]:
            print(Template("  $tid: $reason").substitute(tid=tid, reason=reason))
        return 1 if len(blocked) > 0 else 0
    print("  No se detectaron bloqueos.")
    return 0


def _query_similar(qarg: str, tasks: list[dict]) -> int:
    """Query: find tasks with similar objectives."""
    qwords = set(qarg.lower().split())
    scored = [
        (
            len(qwords & set((t.get("objective") or "").lower().split())),
            t.get("task_id", "?"),
            (t.get("objective") or "")[:80],
        )
        for t in tasks
    ]
    scored = [(s, tid, o) for s, tid, o in scored if s >= 2]
    if scored:
        scored.sort(reverse=True)
        print(Template("Tareas similares a '$q':").substitute(q=qarg[:50]))
        for score, tid, obj in scored[:5]:
            print(
                Template("  $tid (score=$s): $obj").substitute(
                    tid=tid, s=str(score), obj=obj
                )
            )
    else:
        print("No se encontraron tareas similares.")
    return 0


_QUERY_DISPATCH = {
    "task": _query_task,
    "file": _query_file,
    "status": _query_status,
    "blocked": _query_blocked,
    "similar": _query_similar,
}


def cmd_query(sub_args: list[str]) -> int:
    """walker query: task|file|similar <arg> [<chain>] [flags]."""
    if len(sub_args) < 1:
        eprint(
            "Uso: walker.py query task|file|similar|status|blocked <arg> [<chain>] [flags]"
        )
        return 2

    qtype = sub_args[0]
    qarg = sub_args[1] if len(sub_args) > 1 else ""
    rest = sub_args[2:] if len(sub_args) > 2 else []

    chain_dir = None
    # For status/blocked, the first positional arg IS the chain_dir
    if qtype in ("status", "blocked") and qarg and not qarg.startswith("--"):
        chain_dir = os.path.abspath(qarg)
        qarg = ""
    elif rest and not rest[0].startswith("--"):
        chain_dir = os.path.abspath(rest[0])
        rest = rest[1:]
        if chain_dir and not _is_chain_dir(chain_dir):
            return _list_project_sprints(chain_dir)

    positional, flags = _parse_args(rest)
    if not chain_dir:
        eprint("Se requiere un chain_dir valido.")
        return 2

    tasks, err = _load_tasks(chain_dir)
    if err:
        eprint(err)
        return 2

    if qtype == "status":
        return _query_status(chain_dir, tasks)
    elif qtype == "blocked":
        return _query_blocked(tasks)

    if not handler:
        eprint(Template("Tipo de busqueda invalido: $t").substitute(t=qtype))
        eprint("  Validos: task, file, similar, status, blocked")
        return 2

    if qtype == "task":
        return _query_task(
            qarg,
            tasks,
            flags,
            "--files" in sub_args or "--context" in sub_args,
            chain_dir,
        )
    elif qtype == "file":
        return _query_file(qarg, tasks)
    else:
        return _query_similar(qarg, tasks)


def _check_task_status_relevance(
    chain_dir: str, task_id: str, warnings: list[str]
) -> None:
    """Warning si la task_id actual ya esta en estado done."""
    from features.delta_tracking.application.services.spring_service import (
        SpringService,
    )

    try:
        spring_svc = SpringService.from_chain_dir(chain_dir)
        if spring_svc:
            spring_id = spring_svc.get_spring_id(chain_dir)
            spring_svc.ensure_spring_imported(chain_dir, spring_id)
            spec = spring_svc.get_spec(spring_id, task_id)
            status = spec.get("status", "") if spec else ""
        else:
            status = ""
    except Exception:
        status = ""
    if isinstance(status, str) and status.lower() in _DONE_STATUSES:
        warnings.append(
            Template("task_id '$tid' ya esta $status.").substitute(
                tid=task_id, status=status
            )
        )


def cmd_health(sub_args: list[str]) -> int:
    """walker health: diagnostica inconsistencias en el estado del workflow.

    Uso:
      walker.py health [<chain_dir> | --project <id>]
    """
    sub_args, chain_dir = extract_chain_dir(sub_args)
    if chain_dir and not _is_chain_dir(chain_dir):
        return _list_project_sprints(chain_dir)

    if not chain_dir:
        positional, flags = _parse_args(sub_args)
        project_flag = flags.get("project", None)
        if project_flag:
            sprints = _find_active_sprints()
            ps = [s for s in sprints if s["project_id"] == project_flag]
            if not ps:
                eprint(
                    Template("No hay sprints activos para '$pid'.").substitute(
                        pid=project_flag
                    )
                )
                return 1
            chain_dir = ps[0]["chain_dir"]
        else:
            eprint("Uso: walker.py health [<chain_dir> | --project <id>]")
            return 2

    state = ensure_state(chain_dir)
    workflow = find_workflow(chain_dir)
    plan_path = os.path.join(chain_dir, "plan.json")

    issues: list[str] = []
    warnings: list[str] = []

    # 1. Current node exists in workflow?
    current = _get_current_node(state)
    sub_key = _get_current_sub_key(state)
    if current not in workflow.get("nodes", {}):
        issues.append(
            Template("Nodo actual '$current' no existe en el workflow.").substitute(
                current=current
            )
        )

    # 2. Check sub_state exists in node
    if current in workflow.get("nodes", {}):
        sub_state = _get_sub_state(workflow, current, sub_key)
        if not sub_state:
            warnings.append(
                Template("Sub-estado '$sub' no existe en el nodo '$node'.").substitute(
                    sub=sub_key, node=current
                )
            )

    # 3. Task_id exists in plan.json?
    task_id = state.get("context", {}).get("task_id")
    if task_id and os.path.exists(plan_path):
        pd = load_json(plan_path)
        if pd:
            pl = pd.get("plan", pd)
            found = any(t.get("task_id") == task_id for t in pl.get("tasks", []))
            if not found:
                warnings.append(
                    Template("task_id '$tid' no existe en plan.json.").substitute(
                        tid=task_id
                    )
                )

    # 4. Task_id is still relevant (not done)?
    if task_id and chain_dir:
        _check_task_status_relevance(chain_dir, task_id, warnings)

    # 5. History is not empty
    if not state.get("history"):
        warnings.append("El historial esta vacio. Puede indicar un reset reciente.")

    # 6. Workflow has nodes
    if not workflow.get("nodes"):
        issues.append("El workflow no tiene nodos definidos.")

    # Print report
    print("=" * 70)
    print("  WALKER HEALTH (v4)")
    print("=" * 70)
    print()
    print(
        Template("  Current: [$current.$sub_key]").substitute(
            current=current, sub_key=sub_key
        )
    )
    print(Template("  Task ID: $tid").substitute(tid=task_id or "(ninguno)"))
    print(
        Template("  Nodos en workflow: $n").substitute(
            n=str(len(workflow.get("nodes", {})))
        )
    )
    print()

    _print_health_report(issues, warnings)

    return 1 if issues else 0


def _print_health_report(issues: list[str], warnings: list[str]) -> None:
    """Imprime el reporte de salud (issues/warnings/sugerencias)."""
    if not issues and not warnings:
        print("  ✅ Todo consistent. No se detectaron problemas.")
        print()
        return

    if issues:
        print("  ❌ PROBLEMAS detectados:")
        for i in issues:
            print(Template("    - $msg").substitute(msg=i))
        print()

    if warnings:
        print("  ⚠️  ADVERTENCIAS:")
        for w in warnings:
            print(Template("    - $msg").substitute(msg=w))
        print()

    # Suggest recovery
    print("  Sugerencias:")
    if issues:
        print("    walker.py reset       → Volver a INIT")
        print("    walker.py goto <state> → Forzar un estado valido")
    if warnings:
        print("    walker.py status      → Verificar estado actual")
    print()


def cmd_backlog(sub_args: list[str]) -> int:
    """walker backlog: list, move, remove tareas del backlog del proyecto.

    Uso:
      walker.py backlog list <chain_dir>
      walker.py backlog move <chain_dir> <task_id>
    """
    if len(sub_args) < 2:
        eprint("Uso: walker.py backlog list|move <chain_dir> [<task_id>]")
        return 2

    sub = sub_args[0]
    chain_dir = os.path.abspath(sub_args[1]) if len(sub_args) > 1 else None
    task_id = sub_args[2] if len(sub_args) > 2 else None

    if not chain_dir or not os.path.isdir(chain_dir):
        eprint("Se requiere un chain_dir valido.")
        return 2

    if sub == "list":
        items = _load_backlog(chain_dir)
        if not items:
            print("  No hay tareas en el backlog.")
            return 0
        print(Template("  Backlog ($n tareas):").substitute(n=str(len(items))))
        for item in items:
            tid = item.get("task_id", "?")
            obj = (item.get("objective") or "")[:80]
            origin = item.get("_origin_sprint", "?")
            print(Template("  $tid  $obj").substitute(tid=tid.ljust(12), obj=obj))
            print(Template("    Origen: $origin").substitute(origin=origin))
        return 0

    elif sub == "move" and task_id:
        plan_path = os.path.join(chain_dir, "plan.json")
        plan_raw = load_json(plan_path)
        if not plan_raw:
            eprint("plan.json no encontrado.")
            return 2
        pl = plan_raw.get("plan", plan_raw)
        task = None
        for t in pl.get("tasks", []):
            if t.get("task_id") == task_id:
                task = t
                break
        if not task:
            eprint(Template("Tarea no encontrada: $tid").substitute(tid=task_id))
            return 2

        backlog = _load_backlog(chain_dir)
        task["_origin_sprint"] = os.path.basename(chain_dir)
        task["_moved_to_backlog_at"] = datetime.now(timezone.utc).isoformat()
        backlog.append(task)
        _save_backlog(chain_dir, backlog)
        # Mark as backlog en el DELTA (nodo spec — fuente unica)
        try:
            from features.delta_tracking.application.services.spring_service import (
                SpringService,
            )

            spring_svc = SpringService.from_chain_dir(chain_dir)
            if spring_svc:
                spring_id = spring_svc.get_spring_id(chain_dir)
                spring_svc.ensure_spring_imported(chain_dir, spring_id)
                spring_svc.update_spec_status(spring_id, task_id, "backlog")
        except Exception as e:
            eprint(f"  [warn] No se pudo marcar backlog en el delta: {e}")
        print(Template("  [backlog] $tid movida al backlog").substitute(tid=task_id))
        return 0

    else:
        eprint("Uso: walker.py backlog list|move <chain_dir> [<task_id>]")
        return 2


def cmd_verify(sub_args: list[str]) -> int:
    """walker verify: verifica predicates de una tarea contra codigo real.

    Uso: walker.py verify <chain_dir> <task_id>
    """
    if len(sub_args) < 2:
        eprint("Uso: walker.py verify <chain_dir> <task_id>")
        return 2
    chain_dir = os.path.abspath(sub_args[0])
    task_id = sub_args[1]
    if not _is_chain_dir(chain_dir):
        return _list_project_sprints(chain_dir)
    cmd = [sys.executable, _TASK_SCRIPT, "verify", chain_dir, task_id]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.stdout:
        print(r.stdout)
    if r.stderr:
        eprint(r.stderr)
    return r.returncode


def cmd_approve_task(sub_args: list[str]) -> int:
    """walker approve-task: aprueba una tarea para que advance pueda ejecutarse.

    Uso: walker.py approve-task <chain_dir> <task_id>
    """
    if len(sub_args) < 2:
        eprint("Uso: walker.py approve-task <chain_dir> <task_id>")
        return 2
    chain_dir = os.path.abspath(sub_args[0])
    task_id = sub_args[1]
    if not _is_chain_dir(chain_dir):
        return _list_project_sprints(chain_dir)
    cmd = [sys.executable, _SPRING_SCRIPT, "approve-task", chain_dir, task_id]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.stdout:
        print(r.stdout)
    if r.stderr:
        eprint(r.stderr)
    return r.returncode


def _save_knowledge_facts(
    chain_dir: str, node: str, sub_key: str, submitted: dict, task_id: str | None
) -> None:
    """Save knowledge facts from KNOWLEDGE_CAPTURE or KNOWLEDGE_UPDATE to project-level facts.json."""
    # facts.json lives at project level: .cognitive/projects/<project-id>/facts.json
    from features.project import ProjectService

    paths = ProjectService.resolve_paths(chain_dir)
    facts_path = os.path.join(paths.project_root, "facts.json")

    # Read existing facts
    facts: list[dict] = load_json(facts_path) if os.path.exists(facts_path) else []
    if not isinstance(facts, list):
        facts = []
    previous_count = len(facts)

    sprint_id = os.path.basename(chain_dir)

    # Build fact entries based on submitted data
    if sub_key == "KNOWLEDGE_CAPTURE":
        sections = [
            ("artefactos", "artefacto_generado"),
            ("reglas", "regla"),
            ("decisiones", "decision"),
            ("advertencias", "advertencia"),
            ("fuera_scope", "trabajo_fuera_scope"),
        ]
    else:  # KNOWLEDGE_UPDATE
        sections = [
            ("nuevas_reglas", "regla"),
            ("nuevas_advertencias", "advertencia"),
            ("actualizaciones", "actualizacion"),
        ]

    for field, fact_type in sections:
        raw = submitted.get(field, "")
        if not raw:
            continue
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            eprint(f"  [warn] {field} JSON invalido, saltando")
            continue
        if not isinstance(items, list):
            items = [items]

        for item in items:
            if isinstance(item, dict):
                fact = {
                    "categoria": fact_type,
                    "sprint": sprint_id,
                    "task": task_id or "unknown",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    **item,
                }
                facts.append(fact)

    if len(facts) > previous_count:
        try:
            with open(facts_path, "w") as f:
                json.dump(facts, f, indent=2, ensure_ascii=False)
            new_count = len(facts) - previous_count
            print(f"  [facts] {new_count} hecho(s) guardado(s) en facts.json")
        except OSError as e:
            eprint(f"  [warn] Error guardando facts.json: {e}")


def _submit_no_required_fields(
    state: dict, workflow: dict, chain_dir: str, current: str, sub_key: str, task_id: str
) -> int:
    """Submit cuando el sub-estado no requiere campos: avanza + auto-chain."""
    print(
        Template(
            "  [submit] Sub-state '$key' no requiere campos. Avanzando..."
        ).substitute(key=sub_key)
    )
    advance_sub_state(state, workflow)
    save_state(state, chain_dir)
    next_ss = _get_current_sub_state(workflow, state)
    if next_ss and next_ss.get("mode") == "auto":
        if _run_auto_sub_state(state, workflow, chain_dir, current, next_ss, task_id):
            return 0
    _show_on_entry_message(
        workflow["nodes"].get(current, {}),
        chain_dir,
        task_id,
        _get_current_sub_state(workflow, state),
    )
    return 0


def _run_auto_sub_state(
    state: dict, workflow: dict, chain_dir: str, current: str, next_ss: dict, task_id: str
) -> bool:
    """Ejecuta un sub-estado auto y encadena los siguientes. Retorna True si
    ya se completó el flujo (early return)."""
    exec_result = execute_sub_state_action(
        workflow, current, next_ss["key"], chain_dir, task_id
    )
    if exec_result["exit_code"] != 0:
        eprint(
            Template("  [auto] Sub-state '$key' fallo (exit $code)").substitute(
                key=next_ss["key"], code=str(exec_result["exit_code"])
            )
        )
        return _handle_auto_loop(
            state, workflow, chain_dir, current, next_ss, exec_result, task_id
        )
    if not advance_sub_state(state, workflow):
        print(
            Template("  [submit] Todos los sub-estados completados.").substitute()
        )
        save_state(state, chain_dir)
        _show_on_entry_message(
            workflow["nodes"].get(current, {}),
            chain_dir,
            task_id,
            _get_current_sub_state(workflow, state),
        )
        return True
    save_state(state, chain_dir)
    print(
        Template("  [auto] Sub-state '$key' completado.").substitute(
            key=next_ss["key"]
        )
    )
    # Encadenar mas sub-estados auto
    chain_ss = _get_current_sub_state(workflow, state)
    while chain_ss and chain_ss.get("mode") == "auto":
        chain_result = execute_sub_state_action(
            workflow, current, chain_ss["key"], chain_dir, task_id
        )
        if chain_result["exit_code"] != 0:
            break
        if not advance_sub_state(state, workflow):
            break
        save_state(state, chain_dir)
        print(
            Template("  [auto] Sub-state '$key' completado.").substitute(
                key=chain_ss["key"]
            )
        )
        chain_ss = _get_current_sub_state(workflow, state)
    return False


def cmd_submit(sub_args: list[str]) -> int:
    """walker submit <key=value>...: envia datos para el sub-estado actual.

    Valida required_fields del sub-estado actual. Si todos presentes, avanza
    al siguiente sub-estado. Si incompleto, muestra lista de campos faltantes.

    Uso: walker.py submit [<chain_dir>] [--task_id X] [--key value ...]
    """
    rest_args, chain_dir = extract_chain_dir(list(sub_args))

    positional, flags = _parse_args(rest_args)

    # If chain_dir not extracted from first arg, check positionals (user may have passed it after flags)
    if not chain_dir:
        for p in positional:
            if os.path.isdir(p) and _is_chain_dir(p):
                chain_dir = p
                positional.remove(p)
                break
            resolved = _resolve_chain_id(p)
            if resolved:
                chain_dir = resolved
                positional.remove(p)
                break

    task_id = flags.get("task_id", None)

    state = ensure_state(chain_dir)
    if not task_id:
        task_id = state.get("context", {}).get("task_id", None)
    workflow = find_workflow(chain_dir)
    current = _get_current_node(state)
    sub_key = _get_current_sub_key(state)
    sub_state = _get_current_sub_state(workflow, state)

    if not sub_state:
        eprint(
            Template("No hay sub-estado activo en [$current].").substitute(
                current=current
            )
        )
        return 1

    # Check required_fields
    required = _get_required_fields(workflow, state)
    if not required:
        return _submit_no_required_fields(
            state, workflow, chain_dir, current, sub_key, task_id
        )

    # Validate required fields are present in flags OR already stored
    data_key = f"{current}.{sub_key}"
    stored = state.get("sub_state_data", {}).get(data_key, {})
    missing = [f for f in required if f not in flags and f not in stored]
    if missing:
        eprint("=" * 70)
        eprint(
            Template("  SUBMIT INCOMPLETO para [$current.$sub_key]").substitute(
                current=current, sub_key=sub_key
            )
        )
        eprint(
            Template("  Campos requeridos faltantes: $fields").substitute(
                fields=", ".join(missing)
            )
        )
        eprint("  Usa: submit --<campo> <valor> ...")
        eprint("=" * 70)
        return 1

    # Merge stored data with new flags (flags override)
    for k in required:
        if k in flags:
            stored[k] = flags[k]

    # Store submitted data (merge with existing stored data)
    data_key = f"{current}.{sub_key}"
    state.setdefault("sub_state_data", {})[data_key] = stored
    print(
        Template("  [submit] Datos guardados para $key: $data_keys").substitute(
            key=data_key, data_keys=str(list(stored.keys()))
        )
    )

    # ── Save knowledge facts if KNOWLEDGE_CAPTURE or KNOWLEDGE_UPDATE ──
    if sub_key in ("KNOWLEDGE_CAPTURE", "KNOWLEDGE_UPDATE") and chain_dir:
        _save_knowledge_facts(chain_dir, current, sub_key, stored, task_id)

    # Advance to next sub-state
    advanced = advance_sub_state(state, workflow)
    save_state(state, chain_dir)

    if advanced:
        next_ss = _get_current_sub_state(workflow, state)
        print(
            Template("  [submit] Avanzando a [$current.$next_key]").substitute(
                current=current, next_key=next_ss.get("key", "")
            )
        )
        # If next sub-state is auto, execute it
        if next_ss and next_ss.get("mode") == "auto":
            handled = _run_auto_sub_state(
                state, workflow, chain_dir, current, next_ss, task_id
            )
            if handled:
                return 0
        _show_on_entry_message(
            workflow["nodes"].get(current, {}),
            chain_dir,
            task_id,
            _get_current_sub_state(workflow, state),
        )
    else:
        print(
            Template(
                "  [submit] Todos los sub-estados de [$current] completados."
            ).substitute(current=current)
        )
        # Auto-transition: if only one valid outgoing gate, use it
        trans = valid_transitions(workflow, current)
        inter_node_trans = [t for t in trans if t.get("gate") and t["to"] != current]
        if len(inter_node_trans) == 1:
            auto_gate = inter_node_trans[0]["gate"]
            print(
                Template(
                    "  [submit] Auto-transicionando via [$gate] -> [$to]"
                ).substitute(gate=auto_gate, to=inter_node_trans[0]["to"])
            )
            print()
            # Build sub_args for cmd_transition
            t_args = [auto_gate]
            if chain_dir:
                t_args.append(chain_dir)
            if task_id:
                t_args.extend(["--task_id", task_id])
            return cmd_transition(t_args)
        elif len(inter_node_trans) > 1:
            print("  Transiciones disponibles:")
            for t in inter_node_trans:
                print(
                    Template("    submit (auto) -> [$to] via [$gate]").substitute(
                        to=t["to"], gate=t["gate"]
                    )
                )
            print()
        else:
            print("  (sin transiciones inter-nodo disponibles)")

    return 0


# ═══════════════════════════════════════════════════════════════
# CPG / CKG QUERY COMMANDS
# ═══════════════════════════════════════════════════════════════


def _cpg_svc(chain_dir: str) -> Any:
    """Lazy import de CpgQueryService."""
    try:
        if _SCRIPT_DIR not in sys.path:
            sys.path.insert(0, _SCRIPT_DIR)
        from features.code_analysis import CpgQueryService

        return CpgQueryService(chain_dir)
    except ImportError as e:
        eprint(f"CpgQueryService no disponible: {e}")
        return None


def cmd_cpg_grep(sub_args: list[str]) -> int:
    """walker cpg-grep: busca en el CPG."""
    rest_args, chain_dir = extract_chain_dir(sub_args)
    if not chain_dir or not rest_args:
        eprint("Uso: walker.py cpg-grep <chain_dir> <query>")
        return 2
    svc = _cpg_svc(chain_dir)
    if not svc:
        return 2
    result = svc.grep(" ".join(rest_args))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_cpg_read(sub_args: list[str]) -> int:
    """walker cpg-read: lee un archivo a traves del CPG."""
    rest_args, chain_dir = extract_chain_dir(sub_args)
    verbose = "--verbose" in rest_args or "-v" in rest_args
    rest_args = [a for a in rest_args if a not in ("--verbose", "-v")]
    if not chain_dir or not rest_args:
        eprint("Uso: walker.py cpg-read <chain_dir> <file_path> [--verbose]")
        return 2
    svc = _cpg_svc(chain_dir)
    if not svc:
        return 2
    result = svc.read(rest_args[0], verbose=verbose)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_deltagraph(sub_args: list[str]) -> int:
    """walker deltagraph: contexto federado (sprint + CPG + CKG)."""
    rest_args, chain_dir = extract_chain_dir(sub_args)
    if not chain_dir:
        eprint("Uso: walker.py deltagraph <chain_dir> [task_id]")
        return 2
    eprint("DeltaGraph no disponible: usa walker <project>/kg_multi")
    return 1


def cmd_cpg_build(sub_args: list[str]) -> int:
    """walker cpg-build: construye o actualiza el CPG."""
    rest_args, chain_dir = extract_chain_dir(sub_args)
    if not chain_dir:
        eprint("Uso: walker.py cpg-build <chain_dir> [--refresh]")
        return 2
    try:
        if _SCRIPT_DIR not in sys.path:
            sys.path.insert(0, _SCRIPT_DIR)
        from features.project import ProjectService
        from features.code_analysis import CPGBuilder

        paths = ProjectService.resolve_paths(chain_dir)
        proj_dir = paths.project_root
        if not proj_dir:
            eprint("No se pudo resolver project_dir")
            return 2
        builder = CPGBuilder(proj_dir)
        result = builder.build()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except Exception as e:
        eprint(f"Error building CPG: {e}")
        return 2


def cmd_ckg_query(sub_args: list[str]) -> int:
    """walker ckg-query: busca en el CKG."""
    rest_args, chain_dir = extract_chain_dir(sub_args)
    if not chain_dir or not rest_args:
        eprint("Uso: walker.py ckg-query <chain_dir> <query>")
        return 2
    try:
        if _SCRIPT_DIR not in sys.path:
            sys.path.insert(0, _SCRIPT_DIR)
        from ckg.application.query import CkgQueryService
    except ImportError:
        eprint("CkgQueryService no disponible")
        return 2

    svc = CkgQueryService(chain_dir)
    query = " ".join(rest_args)
    parts = query.split(" ", 1)
    first = parts[0]

    if first.startswith("neighbors:"):
        result = svc.neighbors(first[len("neighbors:") :])
    elif first.startswith("briefing:"):
        result = svc.briefing(first[len("briefing:") :])
    elif first == "briefing" and len(parts) > 1:
        result = svc.briefing(parts[1])
    else:
        result = svc.query(query)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0
