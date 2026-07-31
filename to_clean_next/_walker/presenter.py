"""
presenter.py — Presentacion del estado del workflow.

SRP: Unica responsabilidad = formatear y mostrar informacion al usuario.
Todas las funciones son pure display (sin logica de dominio).

Extraido de engine.py para separar presentacion de logica.
"""

from __future__ import annotations

import json
import os
from string import Template

from utils import (
    valid_transitions,
)
from .plan_editor import _load_decision_log


# ── Section Header ─────────────────────────────────────────────────────────


def show_section(title: str) -> None:
    """Print a section header with title."""
    print("=" * 70)
    print(Template("  $title").substitute(title=title))
    print("=" * 70)
    print()


# ── Node Header ────────────────────────────────────────────────────────────


def show_node_header(
    current: str,
    node: dict,
    sub_state: dict | None = None,
) -> None:
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


# ── Mode Display ───────────────────────────────────────────────────────────


def show_mode(node: dict, sub_state: dict | None = None) -> None:
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


# ── Context Display ────────────────────────────────────────────────────────


def show_context(state: dict) -> None:
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


# ── Transitions Display ────────────────────────────────────────────────────


def show_transitions(
    workflow: dict,
    node_id: str,
    chain_dir: str | None = None,
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
        freq = gate_counts.get(gate_val, 0)
        pct = int(freq / total * 100) if total > 0 else 0
        freq_display = f"  {pct}%" if freq > 0 else "  —"
        print(
            f"  {i}. transition{gate_display} -> [{t['to']}]{ss_display} {freq_display}"
        )
        print(Template("     -> $desc").substitute(desc=t["gate_description"]))
    print()


# ── History Display ────────────────────────────────────────────────────────


def show_history(state: dict) -> None:
    """Print transition history (node.sub_state format)."""
    hist = state.get("history", [])
    if hist:
        print("  History:")
        print(Template("    $hist").substitute(hist=" -> ".join(hist)))
        print()


# ── Sprint Info ────────────────────────────────────────────────────────────


def show_sprint_info(chain_dir: str) -> None:
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


# ── Related Results Display ────────────────────────────────────────────────


def show_related_results(related: dict, deep: bool) -> None:
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


# ── Flow Progress Display ──────────────────────────────────────────────────


def show_flow_progress(state: dict) -> None:
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


# ── On-Entry Message (delega en OrchestrationService para prompt_id) ────────


def _resolve_prompt_id_to_message(
    prompt_id: str,
    chain_dir: str | None,
    task_id: str | None = None,
) -> str:
    """Carga un prompt_id y resuelve placeholders via OrchestrationService.

    Delega en OrchestrationService.resolve_prompt() (TASK-005).
    """
    if not chain_dir:
        return f"[prompt: {prompt_id}]"
    try:
        from _walker.workflow_engine import OrchestrationService

        svc = OrchestrationService()
        return svc.resolve_prompt(prompt_id, chain_dir, task_id=task_id or "")
    except Exception as e:
        return f"[error resolviendo prompt: {e}]"


def show_on_entry_message(
    node: dict,
    chain_dir: str | None = None,
    task_id: str | None = None,
    sub_state: dict | None = None,
) -> None:
    """Print on_entry_message from sub_state (v4) or node (legacy).

    Si el sub-estado tiene prompt_id, carga y resuelve el prompt
    via OrchestrationService en lugar de mostrar on_entry_message inline.
    """
    msg = ""
    if sub_state:
        prompt_id = sub_state.get("prompt_id", "")
        if prompt_id and chain_dir:
            resolved = _resolve_prompt_id_to_message(prompt_id, chain_dir, task_id)
            if resolved and not resolved.startswith("[prompt"):
                msg = resolved
        if not msg:
            msg = sub_state.get("on_entry_message", "")
    if not msg:
        msg = node.get("on_entry_message", "")
    if msg:
        for line in msg.strip().split("\n"):
            print(Template("  $line").substitute(line=line.strip()))
        print()

    # ── Inject relevant knowledge facts ────────────────────────────
    if chain_dir and task_id:
        _inject_knowledge_facts(chain_dir, task_id)


# ── Knowledge Facts Injection ──────────────────────────────────────────────


def _inject_knowledge_facts(chain_dir: str, task_id: str) -> None:
    """Inject relevant knowledge from CKG (ckg.json) + plan.json.

    facts.json is DEPRECATED. Reads from:
    - plan.json: current task's impacted_artifacts.files for file list
    - ckg.json: knowledge nodes (convention, decision, pattern, etc.)
      related to those files (by checking description, file, contains fields)
    """
    project_dir = os.path.dirname(os.path.dirname(chain_dir))
    ckg_path = os.path.join(project_dir, "ckg.json")
    if not os.path.exists(ckg_path):
        return

    # ── Read CKG ────────────────────────────────────────────────────────────
    try:
        with open(ckg_path) as f:
            ckg: dict = json.load(f)
    except (json.JSONDecodeError, OSError):
        return
    all_nodes = ckg.get("nodes", {})
    if not all_nodes:
        return

    # ── Read plan.json for current task's files ────────────────────────────
    plan_path = os.path.join(chain_dir, "plan.json")
    if not os.path.exists(plan_path):
        return
    try:
        with open(plan_path) as f:
            plan = json.load(f)
    except (json.JSONDecodeError, OSError):
        return

    pl = plan.get("plan", plan)
    current_task = None
    for t in pl.get("tasks", []):
        if t.get("task_id") == task_id:
            current_task = t
            break
    if not current_task:
        return

    arts = current_task.get("impacted_artifacts", {})
    task_files = []
    if isinstance(arts, dict):
        task_files = arts.get("files", [])
    elif isinstance(arts, list):
        task_files = arts
    if not task_files:
        return

    # ── Find relevant CKG nodes by file matching ────────────────────────────
    relevant: list[tuple[str, dict]] = []
    for nid, node in all_nodes.items():
        node_text = json.dumps(node).lower()
        for tf in task_files:
            tf_lower = tf.lower()
            if tf_lower in node_text:
                relevant.append((nid, node))
                break
            contains = node.get("contains", [])
            if isinstance(contains, list) and any(
                tf_lower in c.lower() for c in contains
            ):
                relevant.append((nid, node))
                break
            node_file = node.get("file", "")
            if isinstance(node_file, str) and tf_lower in node_file.lower():
                relevant.append((nid, node))
                break

    if not relevant:
        return

    print("  📋 Conocimiento relevante del CKG:")
    for nid, node in relevant[-5:]:  # Max 5 nodes
        ntype = node.get("type", "?")
        label = node.get("label", "")
        desc = node.get("description", "")[:120]
        src = node.get("metadata", {}).get("task_id", "?")
        sprint = (
            node.get("metadata", {}).get("source", "").split(":")[-1]
            if ":" in node.get("metadata", {}).get("source", "")
            else "?"
        )
        if label:
            print(f"    • [{src} ({sprint})] [{ntype}] {label}")
        elif desc:
            print(f"    • [{src} ({sprint})] [{ntype}] {desc[:100]}")
    print()


# ── Entry Message (legacy) ─────────────────────────────────────────────────


def show_entry_message(node: dict, was_executed: bool) -> None:
    """Show on_entry_message if entry_action was not auto-executed."""
    if was_executed:
        return
    msg = node.get("on_entry_message", "")
    if not msg:
        return
    for line in msg.strip().split("\n"):
        print(Template("  $line").substitute(line=line.strip()))
