"""
engine — find workflow module

Extracted from walker.py.
"""

from utils import (
    eprint,
    load_json,
    _get_sub_state,
    _get_node_sub_states,
    state_path,
    save_state,
    _resolve_task_id,
    run_script,
    _V3_NODE_NAMES,
    _INIT_SCRIPT,
    _DONE_STATUSES,
    valid_transitions,
)
import os
import json
from datetime import datetime, timezone
from string import Template
from .migrate import _migrate_workflow, _emergency_workflow, _presprint_workflow
from .plan_editor import _load_decision_log


def find_workflow(chain_dir: str | None = None) -> dict:
    """Load the project workflow.json or return minimal fallback with instructions."""
    if chain_dir:
        from _walker.shared.path_resolver import resolve_project_root, resolve_project_id

        parent = resolve_project_root(chain_dir)
        workflow_path = os.path.join(parent, "workflow.json")
        if os.path.exists(workflow_path):
            data = load_json(workflow_path)
            if data:
                # Auto-migrate: add missing nodes/edges from canonical set
                if _migrate_workflow(workflow_path, data):
                    data = load_json(workflow_path)
                return data

        # No workflow.json — give the LLM clear creation instructions
        project_dir = resolve_project_root(chain_dir)  # .cognitive/projects/<pid>
        project_id = resolve_project_id(chain_dir) or "unknown"
        init_script = _INIT_SCRIPT
        print()
        print("=" * 70)
        print("  ⚠️  WORKFLOW NO ENCONTRADO")
        print("=" * 70)
        print()
        print(
            Template("  No hay workflow.json para el proyecto '$pid'.").substitute(
                pid=project_id
            )
        )
        print("  El workflow define el proceso (5 nodos, sub-estados deterministas).")
        print("  Sin el, walker.py usa un workflow minimo de emergencia.")
        print()
        print("  PARA CREARLO, ejecuta este comando:")
        print()
        print(
            Template("    python3 $script $dir").substitute(
                script=init_script, dir=project_dir
            )
        )
        print()
        print("  Esto generara .cognitive/projects/<pid>/workflow.json")
        print("  con el workflow v4 (5 nodos con sub-estados secuenciales).")
        print("  Luego vuelve a ejecutar walker.py.")
        print()
        print("  MIENTRAS TANTO, se usara un workflow minimo de emergencia")
        print("  para que puedas comenzar.")
        print("=" * 70)
        print()
        return _emergency_workflow()
    return _presprint_workflow()


def ensure_state(chain_dir: str | None = None) -> dict:
    """Read walker state from sprint-state.json or reconstruct from sprint-state.json, or return ephemeral default."""
    if chain_dir:
        sp = state_path(chain_dir)
        if sp and os.path.exists(sp):
            state = load_json(sp)
            if state:
                # normalize v4 wrapper: prefer root keys, fallback to legacy/nested state
                if not state.get("current_node") and state.get("walker"):
                    state = dict(state.get("walker", {}))
                return state

        reconstructed = _reconstruct_walker_state(chain_dir)
        if reconstructed:
            save_state(reconstructed, chain_dir)
            return reconstructed

    return {
        "current_node": "SPRINT",
        "current_sub_state": "INIT",
        "sub_state_index": 0,
        "history": ["SPRINT.INIT"],
        "sub_state_data": {},
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "context": {"chain_dir": chain_dir, "task_id": None},
    }


def _reconstruct_walker_state(chain_dir: str) -> dict | None:
    """Reconstruct walker state from sprint-state.json when .delta-walker.json is missing (v4)."""
    sprint_state_path = os.path.join(chain_dir, "sprint-state.json")
    if not os.path.exists(sprint_state_path):
        return None

    try:
        with open(sprint_state_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return None

    prog = data.get("progress", data)
    current = prog
    task_states = current.get("task_states", {})
    finished = current.get("finished", False)

    total_tasks = len(task_states)
    completed_tasks = sum(
        1
        for s in task_states.values()
        if isinstance(s, str) and s.lower() in _DONE_STATUSES
    )
    has_pending = total_tasks > completed_tasks

    # Map to v4 state
    if finished or (completed_tasks == total_tasks and total_tasks > 0):
        node, sub_state, idx = "SPRINT", "CLOSE_CHECK", 5
    elif has_pending:
        node, sub_state, idx = "SPRINT", "ACTIVE", 3
    else:
        node, sub_state, idx = "SPRINT", "INIT", 0

    history = [f"{node}.{sub_state}"]
    rejected = _load_rejected_tasks(chain_dir)
    tid = _resolve_task_id(chain_dir, rejected)
    state = {
        "current_node": node,
        "current_sub_state": sub_state,
        "sub_state_index": idx,
        "history": history,
        "sub_state_data": {},
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "context": {
            "chain_dir": chain_dir,
            "task_id": tid,
            "reconstructed": True,
            "reconstructed_from": "sprint-state.json",
            "rejected_tasks": list(rejected),
        },
    }
    return state


def _advance_sub_state(state: dict, workflow: dict) -> bool:
    """Advance to next sub-state in current node. Returns False if already at end."""
    node_id = state.get("current_node", "SPRINT")
    node = workflow.get("nodes", {}).get(node_id, {})
    sub_states = node.get("sub_states", [])
    current_idx = state.get("sub_state_index", 0)
    next_idx = current_idx + 1
    if next_idx < len(sub_states):
        next_ss = sub_states[next_idx]
        state["sub_state_index"] = next_idx
        state["current_sub_state"] = next_ss["key"]
        state["history"].append(f"{node_id}.{next_ss['key']}")
        return True
    return False


def _is_node_complete(workflow: dict, state: dict, node_id: str | None = None) -> bool:
    """Check if all sub-states in the given node are complete (past the last one)."""
    nid = node_id or state.get("current_node", "SPRINT")
    node = workflow.get("nodes", {}).get(nid, {})
    sub_states = node.get("sub_states", [])
    if not sub_states:
        return True
    current_idx = state.get("sub_state_index", 0)
    return current_idx >= len(sub_states)


def validate_gate(
    workflow: dict, node_id: str, gate: str, chain_dir: str | None, task_id: str | None
) -> dict:
    """Run gate_validation script for a gate. Returns {"ok": bool, "message": str}."""
    node = workflow["nodes"].get(node_id, {})
    gv = node.get("gate_validation", {})
    gate_config = gv.get(gate)
    if not gate_config:
        # No validation configured for this gate — allow
        return {"ok": True, "message": ""}

    script = gate_config["script"]
    valid_exit = gate_config.get("valid_exit", [0])
    error_template = gate_config.get("error_message", "Gate validation failed: $stderr")

    result = run_script(script, chain_dir, task_id)

    if result["exit_code"] in valid_exit:
        return {"ok": True, "message": result["stdout"]}

    # Build error message with interpolation
    msg = Template(error_template).safe_substitute(
        stderr=result["stderr"],
        stdout=result["stdout"],
        exit_code=str(result["exit_code"]),
    )
    return {"ok": False, "message": msg}


def execute_sub_state_action(
    workflow: dict,
    node_id: str,
    sub_key: str,
    chain_dir: str | None,
    task_id: str | None,
) -> dict:
    """Execute auto_complete for a mode:auto sub-state. Returns script result."""
    ss = _get_sub_state(workflow, node_id, sub_key)
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

    result = run_script(script, chain_dir, task_id)
    print(Template("  [auto_complete] $action").substitute(action=script))
    if result["stdout"]:
        for line in result["stdout"].split("\n"):
            print(Template("    | $line").substitute(line=line))
    if result["stderr"]:
        for line in result["stderr"].split("\n"):
            eprint(Template("    ! $line").substitute(line=line))
    print()

    return {
        "executed": True,
        "exit_code": result["exit_code"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }


def _handle_auto_loop(
    state: dict,
    workflow: dict,
    chain_dir: str | None,
    current: str,
    failed_ss: dict,
    exec_result: dict,
    task_id: str | None = None,
) -> bool:
    """If sub-state has on_failure_loop, go back to previous sub-state.

    Permite auto-loop: cuando un sub-estado mode:auto con on_failure_loop
    falla (exit != 0), retrocede al sub-estado anterior para re-intentar.
    Usado en CODE_REVIEW_SCAN para loopear a WORK hasta pasar.
    Returns True if loop was handled, False otherwise.
    """
    if not failed_ss.get("on_failure_loop"):
        return False
    if not chain_dir:
        return False

    prev_idx = state.get("sub_state_index", 0) - 1
    if prev_idx < 0:
        return False  # Can't loop back from first sub-state

    node = workflow.get("nodes", {}).get(current, {})
    sub_states = node.get("sub_states", [])
    if prev_idx >= len(sub_states):
        return False

    prev_ss = sub_states[prev_idx]
    state["sub_state_index"] = prev_idx
    state["current_sub_state"] = prev_ss["key"]
    save_state(state, chain_dir)

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
    _show_on_entry_message(
        workflow["nodes"].get(current, {}), chain_dir, task_id, prev_ss
    )
    return True


def execute_auto_action(
    workflow: dict, node_id: str, chain_dir: str | None, task_id: str | None
) -> dict:
    """Legacy: execute entry_action if auto_execute is True. Kept for backward compat."""
    node = workflow["nodes"].get(node_id, {})
    entry = node.get("entry_action")
    if not entry:
        return {
            "executed": False,
            "exit_code": 0,
            "stdout": "No entry_action.",
            "stderr": "",
        }
    auto = node.get("auto_execute", False)
    if not auto:
        return {
            "executed": False,
            "exit_code": 0,
            "stdout": "auto_execute false.",
            "stderr": "",
        }
    result = run_script(entry, chain_dir, task_id)
    print(Template("  [auto_execute] $action").substitute(action=entry))
    if result["stdout"]:
        for line in result["stdout"].split("\n"):
            print(Template("    | $line").substitute(line=line))
    if result["stderr"]:
        for line in result["stderr"].split("\n"):
            eprint(Template("    ! $line").substitute(line=line))
    print()
    return {
        "executed": True,
        "exit_code": result["exit_code"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }


def evaluate_post_gate(
    workflow: dict, node_id: str, chain_dir: str | None, task_id: str | None
) -> str | None:
    """Run post_gate script and return the gate to transition to, or None."""
    node = workflow["nodes"].get(node_id, {})
    pg = node.get("post_gate")
    if not pg:
        return None

    script = pg["script"]
    output_map = pg.get("output_to_gate", {})
    auto_transition = pg.get("auto_transition", False)

    result = run_script(script, chain_dir, task_id)
    output = result["stdout"].strip()

    print(Template("  [post_gate] $script").substitute(script=script))
    if output:
        print(Template("    output: $out").substitute(out=output))
    print()

    # If auto_transition, map output to gate
    if auto_transition and output in output_map:
        return output_map[output]

    return None


def _store_contract_data(
    chain_dir: str | None, task_id: str | None, target_state: str, data: dict
) -> None:
    """Store contract data from submit flags into sprint-state.json.

    El LLM no necesita saber dónde se almacenan los datos.
    Walker usa state.py para persistir en sprint-state.json.
    """
    if not chain_dir or not data:
        return
    from state import load_state, save_state

    s = load_state(chain_dir)
    cd = s.setdefault("walker", {}).setdefault("contract_data", {})
    if task_id:
        cd.setdefault(target_state, {})[task_id] = data
    else:
        cd[target_state] = data
    save_state(s, chain_dir)


def _log_decision(
    chain_dir: str,
    from_state: str,
    gate: str | None,
    task_id: str | None = None,
    reason: str = "",
) -> None:
    """Log a workflow decision to sprint-state.json.walker.decision_log.

    Persiste el contexto de cada decision (rechazo, refine, health_fail, etc.)
    para que los nodos siguientes puedan consultarlo.
    """
    if not chain_dir:
        return
    from state import load_state, save_state
    from datetime import datetime, timezone

    s = load_state(chain_dir)
    log = s.setdefault("walker", {}).setdefault("decision_log", [])
    entry = {
        "from": from_state,
        "gate": gate,
        "task_id": task_id,
        "reason": reason,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    log.append(entry)
    save_state(s, chain_dir)


def _show_section(title: str) -> None:
    print("=" * 70)
    print(Template("  $title").substitute(title=title))
    print("=" * 70)
    print()


def _show_node_header(current: str, node: dict, sub_state: dict | None = None) -> None:
    """Print current state header with node, sub-state, and description."""
    ss_key = sub_state.get("key", "") if sub_state else ""
    if ss_key:
        print(
            Template("  Current:  [$current.$ss_key]").substitute(
                current=current, ss_key=ss_key
            )
        )
    else:
        print(Template("  Current:  [$current]").substitute(current=current))
    label = node.get("label", "")
    if label:
        print(Template("  $label").substitute(label=label))
    for line in node.get("description", "").split("\n"):
        print(Template("     $line").substitute(line=line.strip()))
    if sub_state:
        mode = sub_state.get("mode", "manual")
        req = sub_state.get("required_fields", [])
        fields_str = ", ".join(req) if req else "(ninguno)"
        print(
            Template("  Sub-state: $key  mode=$mode  required=[$fields]").substitute(
                key=sub_state["key"], mode=mode, fields=fields_str
            )
        )
    print()


def _show_mode(node: dict, sub_state: dict | None = None) -> None:
    """Print the execution mode of the current sub-state (v4)."""
    if sub_state:
        mode = sub_state.get("mode", "manual")
        if mode == "auto":
            script = sub_state.get("auto_complete", "")
            print(
                Template("  Mode: auto  (walker ejecuta: $script)").substitute(
                    script=script
                )
            )
        else:
            req = sub_state.get("required_fields", [])
            if req:
                print(
                    Template("  Mode: manual  (submit required: $fields)").substitute(
                        fields=", ".join(req)
                    )
                )
            else:
                print("  Mode: manual  (submit sin campos requeridos)")
    else:
        print("  Mode: legacy (sin sub-estados)")
    print()


def _show_context(state: dict) -> None:
    """Print chain_dir and task_id context if present."""
    ctx = state.get("context", {})
    if not ctx.get("chain_dir") and not ctx.get("task_id"):
        return
    print("  Context:")
    if ctx.get("chain_dir"):
        print(Template("    chain_dir: $d").substitute(d=ctx["chain_dir"]))
    if ctx.get("task_id"):
        print(Template("    task_id:   $t").substitute(t=ctx["task_id"]))
    print()


def _show_transitions(
    workflow: dict, node_id: str, chain_dir: str | None = None
) -> None:
    """Print valid inter-node transitions from the current node with Markov probabilities."""
    trans = valid_transitions(workflow, node_id)

    # Compute Markov transition frequencies from decision_log
    gate_counts: dict[str, int] = {}
    if chain_dir:
        log = _load_decision_log(chain_dir)
        for entry in log:
            if entry.get("from") == node_id and entry.get("gate"):
                g = entry["gate"]
                gate_counts[g] = gate_counts.get(g, 0) + 1
    total = sum(gate_counts.values()) if gate_counts else 1

    print(Template("  Inter-node transitions ($n):").substitute(n=len(trans)))
    if not trans:
        print("    (ninguna — flujo terminal)")
    for i, t in enumerate(trans, 1):
        gate_val = t.get("gate", "")
        gate_display = f" [{gate_val}]" if gate_val else ""
        ss_gate = t.get("sub_state_gate", "")
        ss_display = f" ->.{ss_gate}" if ss_gate else ""
        # Markov frequency
        freq = gate_counts.get(gate_val, 0)
        pct = int(freq / total * 100) if total > 0 else 0
        freq_display = f"  {pct}%" if freq > 0 else "  —"
        print(
            f"  {i}. transition{gate_display} -> [{t['to']}]{ss_display} {freq_display}"
        )
        print(Template("     -> $desc").substitute(desc=t["gate_description"]))
    print()


def _show_history(state: dict) -> None:
    """Print transition history (node.sub_state format)."""
    hist = state.get("history", [])
    if hist:
        print("  History:")
        print(Template("    $hist").substitute(hist=" -> ".join(hist)))
        print()


def _show_sprint_info(chain_dir: str) -> None:
    """Print sprint summary (id, status, task count)."""
    from state import load_state

    s = load_state(chain_dir)
    tasks = s.get("tasks", {})
    total = len(tasks)
    done = sum(
        1
        for t in tasks.values()
        if isinstance(t, dict) and t.get("status") == "completed"
    )
    # Read status from any known location (v4 state, sprint.status, meta.status)
    status = (
        s.get("state")
        or s.get("sprint", {}).get("status")
        or s.get("meta", {}).get("status")
        or "?"
    )
    print(
        Template("  Sprint: $id  status=$st  tasks=$done/$total").substitute(
            id=s.get("sprint", {}).get(
                "chain_id",
                s.get("meta", {}).get("chain_id", os.path.basename(chain_dir)),
            ),
            st=status,
            done=done,
            total=total or "?",
        )
    )


def _show_related_results(related: dict, deep: bool) -> None:
    """Print related tasks grouped by match type."""
    has_any = any(related.values())
    if not has_any:
        return
    print("  ── CONTEXTO HISTORICO ──")
    if related["file"]:
        print("  Por archivos compartidos:")
        for s, t in sorted(related["file"]):
            print(Template("    $sprint > $tid").substitute(sprint=s, tid=t))
    if deep:
        if related["domain"]:
            print("  Mismo dominio/tipo:")
            for s, t in sorted(related["domain"]):
                print(Template("    $sprint > $tid").substitute(sprint=s, tid=t))
        if related["kw"]:
            print("  Objetivos similares:")
            for s, t in sorted(related["kw"]):
                print(Template("    $sprint > $tid").substitute(sprint=s, tid=t))
    print()


def _show_flow_progress(state: dict) -> None:
    """Show v4 development flow progress: which major phases have been completed."""
    history = state.get("history", [])
    history_set = set(history)

    flow = [
        ("ANALYZE.GATHER", "Investigacion", "research"),
        ("ANALYZE.REVIEW", "Analisis", "analysis"),
        ("IMPLEMENT.WORK", "Implementacion", "code"),
        ("IMPLEMENT.REPORT_GATE", "Report Gate", "report"),
        ("VERIFY.QA", "QA", "qa"),
        ("VERIFY.PO_ACCEPT", "PO acceptance", "po_accept"),
    ]

    done = 0
    total = len(flow)
    lines = []
    for step, label, key in flow:
        if step in history_set:
            lines.append(Template("    ✅ $label").substitute(label=label))
            done += 1
        else:
            lines.append(Template("    ⬜ $label").substitute(label=label))

    pct = int(done / total * 100) if total > 0 else 0
    bar = "▓" * (pct // 10) + "░" * (10 - pct // 10)
    print()
    print("  ── FLOW PROGRESS (v4) ──")
    for l in lines:
        print(l)
    print(
        Template("  $bar  $pct% completado ($done/$total pasos)").substitute(
            bar=bar, pct=str(pct), done=str(done), total=str(total)
        )
    )
    print()


def _show_on_entry_message(
    node: dict,
    chain_dir: str | None = None,
    task_id: str | None = None,
    sub_state: dict | None = None,
) -> None:
    """Print on_entry_message from sub_state (v4) or node (legacy)."""
    # Prefer sub_state's on_entry_message, fallback to node's
    msg = ""
    if sub_state:
        msg = sub_state.get("on_entry_message", "")
    if not msg:
        msg = node.get("on_entry_message", "")
    if msg:
        for line in msg.strip().split("\n"):
            print(Template("  $line").substitute(line=line.strip()))
        print()

    # ── Inject relevant knowledge facts ────────────────────────────
    # When entering a sub-state with a known task, look up facts
    # from previous tasks that touched the same artefacts.
    if chain_dir and task_id:
        _inject_knowledge_facts(chain_dir, task_id)


def _inject_knowledge_facts(chain_dir: str, task_id: str) -> None:
    """Inject relevant facts from facts.json based on current task artefactos."""
    import json as _json

    # facts.json at project level
    project_dir = os.path.dirname(os.path.dirname(chain_dir))
    facts_path = os.path.join(project_dir, "facts.json")
    if not os.path.exists(facts_path):
        return

    try:
        with open(facts_path) as f:
            all_facts: list[dict] = _json.load(f)
    except (_json.JSONDecodeError, OSError):
        return
    if not isinstance(all_facts, list):
        return

    # Read plan.json to get current task's artefactos
    plan_path = os.path.join(chain_dir, "plan.json")
    if not os.path.exists(plan_path):
        return
    try:
        with open(plan_path) as f:
            plan = _json.load(f)
    except (_json.JSONDecodeError, OSError):
        return

    pl = plan.get("plan", plan)
    current_task = None
    for t in pl.get("tasks", []):
        if t.get("task_id") == task_id:
            current_task = t
            break
    if not current_task:
        return

    # Get current task artefactos
    arts = current_task.get("impacted_artifacts", {})
    task_files = []
    if isinstance(arts, dict):
        task_files = arts.get("files", [])
    elif isinstance(arts, list):
        task_files = arts

    if not task_files:
        return

    # Find facts matching current task artefactos
    relevant = []
    for fact in all_facts:
        # Collect related artefacts from fact
        fact_artefactos = []
        # Check various fields that might contain file references
        for src_field in ["archivos_relacionados", "archivo"]:
            val = fact.get(src_field)
            if isinstance(val, list):
                fact_artefactos.extend(val)
            elif isinstance(val, str):
                fact_artefactos.append(val)
        # Also include any artefact names embedded in the fact content
        for k, v in fact.items():
            if k not in (
                "categoria",
                "ts",
                "task",
                "sprint",
                "regla",
                "decision",
                "advertencia",
                "proposito",
                "contexto",
            ):
                if isinstance(v, str) and v.endswith(".py"):
                    fact_artefactos.append(v)

        if not fact_artefactos:
            continue

        for fa in fact_artefactos:
            # Match if fact's artefact is substring of task's artefact or vice versa
            for tf in task_files:
                if fa and tf and (fa in tf or tf in fa):
                    relevant.append(fact)
                    break
            if fact in relevant:
                break

    if not relevant:
        return

    print("  📋 Facts relevantes al artefacto de esta tarea:")
    for f in relevant[-5:]:  # Show last 5 at most
        categoria = f.get("categoria", "?")
        contenido = ""
        if categoria == "artefacto_generado":
            contenido = f.get("proposito", "") or f.get("archivo", "")
        elif categoria == "regla":
            contenido = f.get("regla", "")
        elif categoria == "decision":
            contenido = f.get("decision", "")
        elif categoria == "advertencia":
            contenido = f.get("advertencia", "")
        elif categoria == "trabajo_fuera_scope":
            contenido = f"{f.get('archivo', '')}: {f.get('que_hacia', '')}"
        else:
            # Fallback: show first non-metadata field
            meta = {"categoria", "ts", "task", "sprint", "archivo"}
            keys = [k for k in f.keys() if k not in meta]
            contenido = str(f.get(keys[0], "")) if keys else ""

        src = f"{f.get('task', '?')} ({f.get('sprint', '?')})"
        if contenido:
            print(f"    • [{src}] {categoria}: {str(contenido)[:120]}")
    print()


def _apply_state_transition(
    state: dict,
    target: str,
    old_state: str,
    gate: str,
    reason: str,
    chain_dir: str | None,
    task_id: str | None,
) -> None:
    """Update state to a new node, reset sub_state_index, persist."""
    # Resolve first sub-state key from workflow (not assumed INIT)
    workflow = find_workflow(chain_dir)
    target_node = workflow.get("nodes", {}).get(target, {})
    sub_states = target_node.get("sub_states", [])
    first_key = sub_states[0]["key"] if sub_states else "INIT"

    state["current_node"] = target
    state["current_sub_state"] = first_key
    state["sub_state_index"] = 0
    state.setdefault("history", []).append(f"{target}.{first_key}")
    state.setdefault("context", {})
    state.setdefault("sub_state_data", {})
    state.setdefault("audit", {"trail": []})
    state.setdefault("sprint", {})
    if chain_dir:
        state["context"]["chain_dir"] = chain_dir
    if task_id:
        state["context"]["task_id"] = task_id
    save_state(state, chain_dir)

    print(
        Template("[$old] -> [$target] (gate: $gate)").substitute(
            old=old_state, target=target, gate=gate
        )
    )
    if reason:
        print(Template("   Razon: $reason").substitute(reason=reason))
    print()


def _handle_post_gate(
    workflow: dict,
    target: str,
    exec_result: dict,
    chain_dir: str | None,
    task_id: str | None,
) -> int | None:
    """Evaluate post_gate and auto-transition. Returns cmd_transition result or None."""
    has_entry = bool(workflow["nodes"].get(target, {}).get("entry_action"))
    if not exec_result["executed"] and has_entry:
        return None

    auto_gate = evaluate_post_gate(workflow, target, chain_dir, task_id)
    if not auto_gate:
        return None

    print(
        Template("  [post_gate] Auto-transition via: $gate").substitute(gate=auto_gate)
    )
    print()
    return cmd_transition(
        [auto_gate, chain_dir] + (["--task_id", task_id] if task_id else [])
    )


def _show_entry_message(node: dict, was_executed: bool) -> None:
    """Show on_entry_message if entry_action was not auto-executed."""
    if was_executed:
        return
    msg = node.get("on_entry_message", "")
    if not msg:
        return
    for line in msg.strip().split("\n"):
        print(Template("  $line").substitute(line=line.strip()))


def _check_transition_preconditions(
    state: dict, workflow: dict, current: str, gate: str, chain_dir: str | None
) -> tuple[bool, str]:
    """Validate preconditions before allowing inter-node transition.

    Verifica que los sub-estados obligatorios del nodo actual esten
    completos antes de transicionar a otro nodo. Esto evita que el
    agente salte de ANALYZE a IMPLEMENT sin completar el analisis.

    Returns (ok, message).
    """
    # Get the edge definition for this gate
    edge_def = None
    for e in workflow.get("edges", []):
        if (
            e.get("from") == current
            and e.get("gate") == gate
            and e.get("to") != current
        ):
            edge_def = e
            break

    if not edge_def:
        return True, ""  # No edge definition, can't validate

    # Special gates that don't require sub-state completion
    exempt_gates = {"start_planning", "back_to_active", "close_sprint"}
    if gate in exempt_gates:
        return True, ""

    sub_states = _get_node_sub_states(workflow, current)
    stored = state.get("sub_state_data", {})

    # Check each sub-state in current node that has required_fields
    incomplete = []
    for ss in sub_states:
        ss_key = ss.get("key", "")
        req = ss.get("required_fields", [])
        if not req:
            continue  # Auto sub-states or no required data
        data_key = f"{current}.{ss_key}"
        submitted = stored.get(data_key, {})
        missing = [f for f in req if f not in submitted]
        if missing:
            incomplete.append(
                Template("  [$key] incomplete: falta $fields").substitute(
                    key=data_key, fields=", ".join(missing)
                )
            )
        # For decision fields, check value is meaningful
        if "decision" in req and submitted.get("decision") in ("", None):
            incomplete.append(
                Template("  [$key] decision vacia").substitute(key=data_key)
            )

    if incomplete:
        msg = Template("""
  No puedes salir de [$node] via [$gate] porque hay sub-estados
  con datos pendientes:

$details

  Completa los sub-estados con submit antes de transition.
""").substitute(node=current, gate=gate, details="\n".join(incomplete))
        return False, msg

    # Specific gate preconditions
    if gate == "analysis_approved" and current == "ANALYZE":
        review_key = f"{current}.REVIEW"
        review_data = stored.get(review_key, {})
        if review_data.get("decision") != "approve":
            return False, Template("""
  Gate [$gate]: requiere ANALYZE.REVIEW con decision=approve.
  Estado actual: decision='$dec'
  Completa el analisis y apruebalo en REVIEW antes de avanzar a IMPLEMENT.
""").substitute(gate=gate, dec=review_data.get("decision", "vacio"))

    return True, ""


def _detect_workflow_version(
    workflow: dict, chain_dir: str | None
) -> tuple[bool, list[str]]:
    """Detect if workflow.json is outdated (not v4)."""
    if not chain_dir:
        return False, []
    version = workflow.get("version", "1.0")
    if version in ("4.0", "4"):
        return False, []
    nodes = workflow.get("nodes", {})
    # Check if it has v3 node names
    missing = [n for n in _V3_NODE_NAMES if n not in nodes]
    if len(missing) < len(_V3_NODE_NAMES):
        return True, missing  # Has v3 nodes, needs migration
    # Unknown format
    return False, []


def _detect_close_check_with_pending(chain_dir: str | None) -> tuple[bool, list[str]]:
    """Detect if sprint has pending tasks during close check."""
    if not chain_dir:
        return False, []
    sprint_state_path = os.path.join(chain_dir, "sprint-state.json")
    if not os.path.exists(sprint_state_path):
        return False, []
    raw = load_json(sprint_state_path)
    if not raw:
        return False, []
    raw.get("progress", raw)
    ts = sprint_state.get("tasks", {})
    pending = [
        tid
        for tid, st in ts.items()
        if isinstance(st, str)
        and st.lower() not in _DONE_STATUSES
        and st.lower() not in ("rejected", "backlog")
    ]
    if pending:
        return True, pending
    return False, []


def _detect_reconstructed_state(state: dict) -> bool:
    """Detect if walker state was reconstructed from sprint-state.json."""
    return state.get("context", {}).get("reconstructed", False)


def _detect_missing_flow_steps(state: dict) -> list[str]:
    """Detect v4 flow steps missing from history."""
    history_set = set(state.get("history", []))
    critical_steps = [
        "IMPLEMENT.USER_GATE",
        "IMPLEMENT.CODE_REVIEW",
        "IMPLEMENT.REPORT_GATE",
        "VERIFY.QA",
        "VERIFY.PO_ACCEPT",
    ]
    missing = [n for n in critical_steps if n not in history_set]
    return missing
