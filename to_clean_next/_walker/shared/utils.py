"""utils.py — Shared utility functions for the walker engine.

SSOT for utility functions migrated from scripts/utils.py.
All functions here should be self-contained or depend only on stdlib.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from string import Template

_SCRIPT_TIMEOUT = 120


# ── Format helpers ──────────────────────────────────────────────────────────


def eprint(*args, **kwargs):
    """Print to stderr."""
    print(*args, file=sys.stderr, **kwargs)


def _report_invalid_gate(workflow: dict, node_id: str, gate: str) -> int:
    """Print error for invalid gate and return exit code 1."""
    trans = _valid_transitions(workflow, node_id)
    valid_gates = [t["gate"] for t in trans if t.get("gate")] + [
        "(sin gate)" for t in trans if not t.get("gate")
    ]
    eprint(
        Template("Gate invalido desde [$node_id]: $gate").substitute(
            node_id=node_id, gate=gate
        )
    )
    eprint(Template("  Gates validos: $gates").substitute(gates=", ".join(valid_gates)))
    return 1


# ── File operations ─────────────────────────────────────────────────────────


def load_json(path: str) -> dict | None:
    """Load and parse a JSON file. Returns None on failure."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def state_path(chain_dir: str | None) -> str | None:
    """Build path to sprint-state.json within a chain directory."""
    if chain_dir:
        return os.path.join(chain_dir, "sprint-state.json")
    return None


def save_state(state: dict, chain_dir: str | None = None) -> None:
    """Persist walker state to chain_dir. No-op without chain_dir."""
    if not chain_dir:
        return
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    sp = state_path(chain_dir)
    if sp:
        os.makedirs(os.path.dirname(sp), exist_ok=True)
        with open(sp, "w") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)


def run_script(
    cmd: str, chain_dir: str | None = None, task_id: str | None = None
) -> dict:
    """Run a shell command with safe interpolation and timeout.

    <chain>/<chain_dir> and <task_id> are interpolated with shlex.quote().
    Also passed as DELTA_CHAIN_DIR / DELTA_TASK_ID environment variables.
    Returns {"exit_code": int, "stdout": str, "stderr": str}.
    """
    env = os.environ.copy()
    if chain_dir:
        cmd = cmd.replace("<chain>", shlex.quote(chain_dir)).replace(
            "<chain_dir>", shlex.quote(chain_dir)
        )
        env["DELTA_CHAIN_DIR"] = chain_dir
    if task_id:
        cmd = cmd.replace("<task_id>", shlex.quote(task_id))
        env["DELTA_TASK_ID"] = task_id

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=_SCRIPT_TIMEOUT,
            env=env,
        )
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": "Timeout ejecutando: " + cmd}
    except Exception as e:
        return {"exit_code": -1, "stdout": "", "stderr": str(e)}


def _is_chain_dir(path: str) -> bool:
    """Check if path looks like a valid sprint chain directory."""
    return os.path.isfile(os.path.join(path, "sprint-state.json"))


# ── Parsing utilities ───────────────────────────────────────────────────────


def _parse_args(args: list[str]) -> tuple[list[str], dict[str, str]]:
    """Split args into (positional, flags_dict)."""
    positional: list[str] = []
    flags: dict[str, str] = {}
    skip_next = False
    for i, a in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if a.startswith("--"):
            key = a[2:]
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                flags[key] = args[i + 1]
                skip_next = True
            else:
                flags[key] = ""
        else:
            positional.append(a)
    return positional, flags


# ── Workflow navigation helpers ─────────────────────────────────────────────


def _get_current_node(state: dict) -> str:
    """Get current node name with backward compat for v3 state files."""
    return state.get("current_node", state.get("current_state", "SPRINT"))


def _get_current_sub_key(state: dict) -> str:
    """Get current sub-state key with backward compat."""
    return state.get("current_sub_state", "INIT")


def _get_sub_state(workflow: dict, node_id: str, sub_key: str) -> dict | None:
    """Get sub-state config by key from a node."""
    node = workflow.get("nodes", {}).get(node_id, {})
    for ss in node.get("sub_states", []):
        if ss.get("key") == sub_key:
            return ss
    return None


def _get_current_sub_state(workflow: dict, state: dict) -> dict | None:
    """Get current sub-state config from walker state."""
    node_id = _get_current_node(state)
    sub_key = _get_current_sub_key(state)
    return _get_sub_state(workflow, node_id, sub_key)


def _get_sub_state_by_index(workflow: dict, node_id: str, idx: int) -> dict | None:
    """Get sub-state at given index in a node."""
    node = workflow.get("nodes", {}).get(node_id, {})
    sub_states = node.get("sub_states", [])
    if 0 <= idx < len(sub_states):
        return sub_states[idx]
    return None


def _get_node_sub_states(workflow: dict, node_id: str) -> list[dict]:
    """Get all sub-states for a node."""
    node = workflow.get("nodes", {}).get(node_id, {})
    return node.get("sub_states", [])


def _get_required_fields(workflow: dict, state: dict) -> list[str]:
    """Get required_fields for current sub-state."""
    ss = _get_current_sub_state(workflow, state)
    if ss:
        return ss.get("required_fields", [])
    return []


def _get_inter_node_edges(workflow: dict, node_id: str) -> list[dict]:
    """Get inter-node edges FROM a given node (for display and navigation)."""
    result = []
    for edge in workflow.get("edges", []):
        if edge["from"] == node_id or edge["from"] == node_id:
            result.append(edge)
    return result


def _valid_transitions(workflow: dict, node_id: str) -> list[dict]:
    """Return valid inter-node transitions from a given node."""
    result = []
    for edge in workflow.get("edges", []):
        if edge["from"] == node_id:
            target = workflow["nodes"].get(edge["to"], {})
            result.append(
                {
                    "to": edge["to"],
                    "label": target.get("label", edge["to"]),
                    "description": target.get("description", ""),
                    "gate": edge.get("gate", ""),
                    "gate_description": edge.get("description", ""),
                    "sub_state_gate": edge.get("sub_state_gate", ""),
                }
            )
    return result


def _find_target(workflow: dict, node_id: str, gate: str) -> str | None:
    """Find target node for a given gate from current node (inter-node edges)."""
    for edge in workflow.get("edges", []):
        if edge["from"] == node_id and edge.get("gate") == gate:
            return edge["to"]
    return None


def _load_tasks(chain_dir: str) -> tuple[list[dict], str]:
    """Load task list from plan.json. Returns (tasks, error_msg)."""
    plan_path = os.path.join(chain_dir, "plan.json")
    if not os.path.exists(plan_path):
        return [], "plan.json no encontrado."
    pd = load_json(plan_path)
    if not pd:
        return [], "Error leyendo plan.json."
    return pd.get("plan", pd).get("tasks", []), ""
