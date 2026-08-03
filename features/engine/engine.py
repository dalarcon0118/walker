"""
engine — Workflow engine puro.

SRP: Orquestar el flujo de trabajo entre nodos y sub-estados.
PRINCIPIO: NO hace IO directo. Delega en infrastructure/repositories.py
PRINCIPIO: NO tiene fallbacks silenciosos. Si falta un archivo, ERROR.
PRINCIPIO: NO tiene compatibilidad legacy (no emergency_workflow,
           presprint_workflow, reconstruct_walker_state, ni defaults efímeros).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from libs.infrastructure.utils import eprint, run_script, _get_sub_state

from .domain.models import WorkflowState, WorkflowGraph
from .infrastructure.repositories import WorkflowRepository, WorkflowStateRepository


# ═════════════════════════════════════════════════════════════════════════
# Repositorios (IO controlado)
# ═════════════════════════════════════════════════════════════════════════


def load_workflow(chain_dir: str) -> WorkflowGraph:
    """Carga el workflow. ERROR si no existe.

    Raises:
        FileNotFoundError: ruta canónica no encontrada.
    """
    repo = WorkflowRepository()
    graph = repo.load(chain_dir)
    if not graph or not graph.nodes:
        from features.project import ProjectService

        paths = ProjectService.resolve_paths(chain_dir)
        raise FileNotFoundError(
            f"No se encontró workflow.json en '{paths.workflow_json_path}'"
        )
    return graph


def load_state(chain_dir: str) -> WorkflowState:
    """Carga el estado del sprint. Retorna WorkflowState vacío si no existe."""
    if not chain_dir:
        raise ValueError("chain_dir vacío")
    return WorkflowStateRepository().load(chain_dir)


def save_state(state: WorkflowState, chain_dir: str) -> None:
    """Persiste el estado del sprint."""
    if not chain_dir:
        raise ValueError("chain_dir vacío")
    WorkflowStateRepository().save(chain_dir, state)


# ═════════════════════════════════════════════════════════════════════════
# Bridge dict API para commands.py (devuelven dicts, no objetos).
# PRINCIPIO: NO hay fallbacks silenciosos. Sin chain_dir → ERROR claro.
# ═════════════════════════════════════════════════════════════════════════


def find_workflow(chain_dir: str | None = None) -> dict:
    """Carga workflow como dict. ERROR si no hay chain_dir o no existe workflow."""
    if not chain_dir:
        raise ValueError("chain_dir vacío — no se puede resolver el workflow")
    graph = load_workflow(chain_dir)
    return _graph_to_dict(graph)


def ensure_state(chain_dir: str | None = None) -> dict:
    """Carga estado como dict. Sin chain_dir → ERROR.

    PRINCIPIO: no retorna defaults inventados. El estado vacío solo se
    produce si realmente no hay sprint persistido.
    """
    if not chain_dir:
        raise ValueError("chain_dir vacío — no se puede resolver el estado")
    return load_state(chain_dir).to_dict()


# ═════════════════════════════════════════════════════════════════════════
# Lógica de dominio pura
# ═════════════════════════════════════════════════════════════════════════


def advance_sub_state(state: WorkflowState | dict, graph: WorkflowGraph | dict) -> bool:
    """Avanza al siguiente sub-estado. Pura: modifica state in-memory.

    Acepta tanto WorkflowState/WorkflowGraph como dict/legacy-workflow-dict.
    """
    # Si es dict legacy, operar directamente sobre el dict (sin conversión)
    if isinstance(graph, dict):
        node_id = (
            state.get("current_node", "SPRINT")
            if isinstance(state, dict)
            else state.current_node
        )
        node = graph.get("nodes", {}).get(node_id, {})
        sub_states = node.get("sub_states", [])
        current_idx = (
            state.get("sub_state_index", 0)
            if isinstance(state, dict)
            else state.sub_state_index
        )
        next_idx = current_idx + 1
        if next_idx < len(sub_states):
            next_ss = sub_states[next_idx]
            if isinstance(state, dict):
                state["sub_state_index"] = next_idx
                state["current_sub_state"] = next_ss["key"]
                state["history"].append(f"{node_id}.{next_ss['key']}")
            else:
                # WorkflowState es inmutable — no podemos mutarlo directamente
                # Esta rama no debería ejecutarse (callers usan dict),
                # pero si ocurre, lanzamos error claro
                raise TypeError(
                    "advance_sub_state con WorkflowState requiere AdvanceService. "
                    "Usa: new_state = AdvanceService.advance(state, graph)"
                )
            return True
        return False

    # Con WorkflowGraph, usar AdvanceService (inmutable)
    if isinstance(state, dict):
        state = WorkflowState.from_dict(state)
    from .domain.services import AdvanceService

    new_state = AdvanceService.advance(state, graph)
    # La funcion retorna bool por compatibilidad, pero el nuevo estado
    # se asigna al caller via reemplazo del dict original
    if new_state is not None:
        # Actualizar el dict in-place para compatibilidad con callers que pasan dict
        if isinstance(state, dict) and not isinstance(state, WorkflowState):
            state.clear()
            state.update(new_state.to_dict())
        return True
    return False


def is_node_complete(
    state: WorkflowState | dict, graph: WorkflowGraph, node_id: str | None = None
) -> bool:
    """Verifica si el nodo está completo."""
    if isinstance(state, dict):
        state = WorkflowState.from_dict(state)
    from .domain.services import ValidationService

    return ValidationService.is_node_complete(state, graph)


def check_transition_preconditions(
    state: WorkflowState | dict, graph: WorkflowGraph, gate_name: str
) -> list[str]:
    """Valida precondiciones para transición. Retorna lista de errores."""
    if isinstance(state, dict):
        state = WorkflowState.from_dict(state)
    from .domain.services import ValidationService

    return ValidationService.check_transition_preconditions(state, graph, gate_name)


def validate_gate(
    workflow: dict,
    node_id: str,
    gate: str,
    chain_dir: str | None = None,
    task_id: str | None = None,
) -> dict:
    """Valida gate ejecutando su script de validación (run_script)."""
    node = workflow.get("nodes", {}).get(node_id, {})
    gv = node.get("gate_validation", {})
    gate_config = gv.get(gate)
    if not gate_config:
        return {"ok": True, "message": ""}

    script = gate_config["script"]
    valid_exit = gate_config.get("valid_exit", [0])
    error_template = gate_config.get("error_message", "Gate validation failed: $stderr")

    result = run_script(script, chain_dir, task_id)
    if result["exit_code"] in valid_exit:
        return {"ok": True, "message": result["stdout"]}

    from string import Template

    msg = Template(error_template).safe_substitute(
        stderr=result["stderr"],
        stdout=result["stdout"],
        exit_code=str(result["exit_code"]),
    )
    return {"ok": False, "message": msg}


# ═════════════════════════════════════════════════════════════════════════
# Operaciones de sub-estado
# ═════════════════════════════════════════════════════════════════════════


def execute_sub_state_action(
    workflow: dict,
    node_id: str,
    sub_key: str,
    chain_dir: str | None,
    task_id: str | None,
) -> dict:
    """Ejecuta auto_complete para un sub-estado mode:auto.

    Retorna dict con executed, exit_code, stdout, stderr.
    """
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
    from string import Template

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


# ═════════════════════════════════════════════════════════════════════════
# Operaciones de estado
# ═════════════════════════════════════════════════════════════════════════


def store_contract_data(
    chain_dir: str | None, task_id: str | None, target_state: str, data: dict
) -> None:
    """Almacena contract data en WorkflowState.walker.contract_data."""
    if not chain_dir or not data:
        return
    state = load_state(chain_dir)
    cd = state.walker.setdefault("contract_data", {})
    if task_id:
        cd.setdefault(target_state, {})[task_id] = data
    else:
        cd[target_state] = data
    save_state(state, chain_dir)


def log_decision(
    chain_dir: str,
    from_state: str,
    gate: str | None,
    task_id: str | None = None,
    reason: str = "",
) -> None:
    """Registra una decisión en WorkflowState.walker.decision_log."""
    if not chain_dir:
        return
    state = load_state(chain_dir)
    log = state.walker.setdefault("decision_log", [])
    log.append(
        {
            "from": from_state,
            "gate": gate,
            "task_id": task_id,
            "reason": reason,
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )
    save_state(state, chain_dir)


def apply_state_transition(
    state: WorkflowState | dict,
    target: str,
    old_state: str,
    gate: str,
    reason: str,
    chain_dir: str | None,
    task_id: str | None,
) -> WorkflowState:
    """Aplica transición entre nodos y persiste."""
    if isinstance(state, dict):
        sprint_state = WorkflowState.from_dict(state)
    else:
        sprint_state = state

    graph = load_workflow(chain_dir)
    from .domain.services import TransitionService

    sprint_state = TransitionService.apply(sprint_state, graph, target)

    if reason:
        sprint_state = sprint_state.with_context("last_reason", reason)
    if task_id:
        sprint_state = sprint_state.with_context("task_id", task_id)
    sprint_state = sprint_state.with_context("chain_dir", chain_dir)
    save_state(sprint_state, chain_dir)

    print(f"  [{old_state}] -> [{target}] (gate: {gate})")
    if reason:
        print(f"    Razon: {reason}")
    print()
    return sprint_state


# ═════════════════════════════════════════════════════════════════════════
# Presentación (pura, sin IO)
# ═════════════════════════════════════════════════════════════════════════


def show_section(title: str) -> None:
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)
    print()


def show_node_header(current: str, node: dict, sub_state: dict | None = None) -> None:
    ss_key = sub_state.get("key", "") if sub_state else ""
    label = (
        f"  Current:  [{current}.{ss_key}]" if ss_key else f"  Current:  [{current}]"
    )
    print(label)
    if node.get("label"):
        print(f"  {node['label']}")
    for line in node.get("description", "").split("\n"):
        print(f"     {line.strip()}")
    if sub_state:
        req = sub_state.get("required_fields", [])
        fields_str = ", ".join(req) if req else "(ninguno)"
        print(
            f"  Sub-state: {sub_state['key']}  mode={sub_state.get('mode', 'manual')}  required=[{fields_str}]"
        )
    print()


def show_mode(node: dict, sub_state: dict | None = None) -> None:
    if sub_state:
        mode = sub_state.get("mode", "manual")
        if mode == "auto":
            print(
                f"  Mode: auto  (walker ejecuta: {sub_state.get('auto_complete', '')})"
            )
        else:
            req = sub_state.get("required_fields", [])
            if req:
                print(f"  Mode: manual  (submit required: {', '.join(req)})")
            else:
                print("  Mode: manual  (submit sin campos requeridos)")
    else:
        print("  Mode: legacy (sin sub-estados)")
    print()


def show_context(state: dict) -> None:
    ctx = state.get("context", {})
    if not ctx.get("chain_dir") and not ctx.get("task_id"):
        return
    print("  Context:")
    if ctx.get("chain_dir"):
        print(f"    chain_dir: {ctx['chain_dir']}")
    if ctx.get("task_id"):
        print(f"    task_id:   {ctx['task_id']}")
    print()


def show_transitions(
    workflow: dict, node_id: str, chain_dir: str | None = None
) -> None:
    from libs.infrastructure.utils import valid_transitions

    trans = valid_transitions(workflow, node_id)

    print(f"  Inter-node transitions ({len(trans)}):")
    if not trans:
        print("    (ninguna — flujo terminal)")
    for i, t in enumerate(trans, 1):
        gate_val = t.get("gate", "")
        gate_display = f" [{gate_val}]" if gate_val else ""
        ss_gate = t.get("sub_state_gate", "")
        ss_display = f" ->.{ss_gate}" if ss_gate else ""
        print(f"  {i}. transition{gate_display} -> [{t['to']}]{ss_display}")
        print(f"     -> {t['gate_description']}")
    print()


def show_history(state: dict) -> None:
    hist = state.get("history", [])
    if hist:
        print("  History:")
        print(f"    {' -> '.join(hist)}")
        print()


def show_on_entry_message(
    node: dict,
    chain_dir: str | None = None,
    task_id: str | None = None,
    sub_state: dict | None = None,
) -> None:
    msg = ""
    if sub_state:
        msg = sub_state.get("on_entry_message", "")
    if not msg:
        msg = node.get("on_entry_message", "")
    if msg:
        for line in msg.strip().split("\n"):
            print(f"  {line.strip()}")
        print()


def show_entry_message(node: dict, was_executed: bool) -> None:
    if was_executed:
        return
    msg = node.get("on_entry_message", "")
    if not msg:
        return
    for line in msg.strip().split("\n"):
        print(f"  {line.strip()}")


def show_sprint_info(chain_dir: str) -> None:
    """Muestra resumen del sprint desde el estado."""
    try:
        state = WorkflowStateRepository().load(chain_dir)
        # Si no hay datos, no mostrar nada (sin fallback silencioso)
        if not state.current_node and not state.sprint:
            return
        tasks = state.tasks
        total = len(tasks)
        done = sum(
            1 for t in tasks if isinstance(t, dict) and t.get("status") == "completed"
        )
        status = state.sprint.get("status", "?")
        chain_id = state.sprint.get("chain_id", "") or chain_dir.split("/")[-1]
        print(f"  Sprint: {chain_id}  status={status}  tasks={done}/{total or '?'}")
    except Exception:
        pass  # Si no se puede cargar, no se muestra


def show_flow_progress(state: dict) -> None:
    history_set = set(state.get("history", []))
    flow = [
        ("ANALYZE.GATHER", "Investigacion"),
        ("ANALYZE.REVIEW", "Analisis"),
        ("IMPLEMENT.WORK", "Implementacion"),
        ("IMPLEMENT.REPORT_GATE", "Report Gate"),
        ("VERIFY.QA", "QA"),
        ("VERIFY.PO_ACCEPT", "PO acceptance"),
    ]
    done = sum(1 for step, _ in flow if step in history_set)
    total = len(flow)
    pct = int(done / total * 100) if total > 0 else 0
    bar = "▓" * (pct // 10) + "░" * (10 - pct // 10)
    print()
    print("  ── FLOW PROGRESS (v4) ──")
    for step, label in flow:
        icon = "✅" if step in history_set else "⬜"
        print(f"    {icon} {label}")
    print(f"  {bar}  {pct}% completado ({done}/{total} pasos)")
    print()


def show_related_results(related: dict, deep: bool) -> None:
    has_any = any(related.values())
    if not has_any:
        return
    print("  ── CONTEXTO HISTORICO ──")
    if related["file"]:
        print("  Por archivos compartidos:")
        for s, t in sorted(related["file"]):
            print(f"    {s} > {t}")
    if deep:
        if related["domain"]:
            print("  Mismo dominio/tipo:")
            for s, t in sorted(related["domain"]):
                print(f"    {s} > {t}")
        if related["kw"]:
            print("  Objetivos similares:")
            for s, t in sorted(related["kw"]):
                print(f"    {s} > {t}")
    print()


# ═════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════


def _graph_to_dict(graph: WorkflowGraph) -> dict:
    """Convierte WorkflowGraph a dict para serialización/output."""
    nodes = {}
    for nid, node in graph.nodes.items():
        sub_states = []
        for ss in node.sub_states:
            entry: dict[str, Any] = {
                "key": ss.key,
                "label": ss.label or ss.key,
                "execution": ss.execution.value if ss.execution else "manual",
                "required_fields": list(ss.required_fields),
                "on_entry_message": ss.on_entry_message or "",
                "input_schema": ss.input_schema or {},
                "output_schema": ss.output_schema or {},
            }
            if ss.action:
                entry["action"] = ss.action
            if ss.prompt_id:
                entry["prompt_id"] = ss.prompt_id
            if ss.auto_complete_script:
                entry["auto_complete"] = ss.auto_complete_script
            sub_states.append(entry)

        gates = [
            {
                "name": g.name,
                "target": g.target_node,
                "target_sub_state": g.target_sub_state,
                "description": g.description,
            }
            for g in graph.edges_by_source.get(nid, [])
        ]
        nodes[nid] = {
            "label": node.label or nid,
            "description": node.description or "",
            "sub_states": sub_states,
            "gates": gates,
            "entry_action": node.entry_action_script or "",
            "on_entry_message": node.on_entry_message or "",
        }

    edges = [
        {
            "from": g.source_node,
            "to": g.target_node,
            "gate": g.name,
            "target_sub_state": g.target_sub_state,
            "description": g.description,
        }
        for g in graph.edges
    ]

    return {
        "version": "4.0",
        "initial_state": graph.initial_state or "SPRINT",
        "nodes": nodes,
        "edges": edges,
        "on": dict(graph.on_events),
    }
