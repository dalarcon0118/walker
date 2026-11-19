"""BDD: contract tests for workflow nodes (SPEC-WALKER-044 P0.3).

Blocking checks:
  a. Every ``NODE.<KEY>.output[.field]`` referenced in a side_effect resolves to
     an existing sub_state and a field declared in its input_schema/output_schema.
  b. Every transform_spec with status=="completed" references completed_at,
     commit_hash, review_verdict, evidence.
  c. Every action declared in any sub_state has a registered handler.

Diagnostic (non-blocking):
  d. JSON keys in a prompt's ``walker <p>/submit '{...}'`` example vs the
     mapped sub_state's input_schema.properties.
"""
from __future__ import annotations

import glob
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODES_DIR = ROOT / "data" / "workflow" / "nodes"
PROMPTS_DIR = ROOT / "fast_mcp" / "features" / "workflow" / "application" / "prompts" / "templates" / "nodes"

NODE_RE = re.compile(r"^NODE\.(.+?)\.output(?:\.(.+))?$")


def _top_level_keys(raw: str) -> set[str]:
    """Extract top-level keys from a JSON-like submit example."""
    keys: set[str] = set()
    depth = 0
    i = 0
    n = len(raw)
    while i < n:
        c = raw[i]
        if c == "{":
            depth += 1
            i += 1
        elif c == "}":
            depth -= 1
            i += 1
        elif c == "[" or c == "]":
            i += 1
        elif c == '"':
            j = i + 1
            buf: list[str] = []
            while j < n and raw[j] != '"':
                if raw[j] == "\\":
                    buf.append(raw[j:j + 2])
                    j += 2
                    continue
                buf.append(raw[j])
                j += 1
            s = "".join(buf)
            k = j + 1
            while k < n and raw[k].isspace():
                k += 1
            if depth == 1 and k < n and raw[k] == ":":
                keys.add(s)
            i = j + 1
        else:
            i += 1
    return keys


def _load_nodes() -> dict:
    out: dict = {}
    for p in sorted(NODES_DIR.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        for ss in data.get("sub_states", []) or []:
            out[ss["key"]] = ss
    return out


def _collect_node_refs(obj) -> list[str]:
    refs: list[str] = []
    if isinstance(obj, str):
        m = NODE_RE.match(obj.strip())
        if m:
            refs.append(obj.strip())
    elif isinstance(obj, dict):
        for v in obj.values():
            refs.extend(_collect_node_refs(v))
    elif isinstance(obj, list):
        for v in obj:
            refs.extend(_collect_node_refs(v))
    return refs


# ── (a) NODE.<KEY>.output[.field] resolution ────────────────────────────────
def test_node_refs_resolve_to_declared_fields():
    submap = _load_nodes()
    violations: list[str] = []
    for p in sorted(NODES_DIR.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        for ss in data.get("sub_states", []) or []:
            for se in ss.get("side_effects", []) or []:
                if not isinstance(se, dict):
                    continue
                params = se.get("params", {})
                if not isinstance(params, dict):
                    params = {}
                for ref in _collect_node_refs(params):
                    m = NODE_RE.match(ref)
                    assert m is not None
                    key = m.group(1)
                    field = m.group(2)
                    target = submap.get(key)
                    if target is None:
                        violations.append(f"{ref}: no sub_state '{key}'")
                        continue
                    props: set[str] = set()
                    for schema_name in ("input_schema", "output_schema"):
                        schema = target.get(schema_name)
                        if isinstance(schema, dict):
                            ps = schema.get("properties")
                            if isinstance(ps, dict):
                                props.update(ps.keys())
                    if field and field not in props:
                        violations.append(
                            f"{ref}: field '{field}' not declared in "
                            f"{target.get('key')} input/output schema"
                        )
    assert not violations, "undeclared NODE refs:\n" + "\n".join(violations)


# ── (b) completed transform_spec references ──────────────────────────────────
def test_completed_transform_spec_references():
    violations: list[str] = []
    required = {"completed_at", "commit_hash", "review_verdict", "evidence"}
    for p in sorted(NODES_DIR.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        for ss in data.get("sub_states", []) or []:
            for se in ss.get("side_effects", []) or []:
                if not isinstance(se, dict) or se.get("type") != "transform_spec":
                    continue
                params = se.get("params", {}) or {}
                if params.get("status") != "completed":
                    continue
                fields = params.get("fields", {}) or {}
                if not isinstance(fields, dict):
                    fields = {}
                missing = required - set(fields.keys())
                if missing:
                    violations.append(
                        f"{ss.get('key')}: completed transform_spec missing {sorted(missing)}"
                    )
    assert not violations, "\n".join(violations)


# ── (c) actions have handlers ────────────────────────────────────────────────
def test_declared_actions_have_handlers():
    from fast_mcp.features.tasks.domain.task_registry import TaskRegistry
    from fast_mcp.features.tasks.handlers import register_all_tasks

    registry = TaskRegistry()
    register_all_tasks(registry, sprint_service=object())

    actions: set[str] = set()
    for p in sorted(NODES_DIR.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        for ss in data.get("sub_states", []) or []:
            action = ss.get("action")
            if action:
                actions.add(str(action))

    missing = sorted(a for a in actions if not registry.has(a))
    assert not missing, f"actions without handler: {missing}"


# ── (d) prompt submit keys vs input_schema (diagnostic only) ─────────────────
def test_prompt_submit_keys_diagnostic():
    submap = _load_nodes()
    pid_to_ss: dict = {}
    for key, ss in submap.items():
        pid = ss.get("prompt_id")
        if pid:
            pid_to_ss[pid] = ss

    violations: list[str] = []
    for prompt_file in sorted(PROMPTS_DIR.rglob("*.prompt.json")):
        try:
            data = json.loads(prompt_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        pid = data.get("id")
        ss = pid_to_ss.get(pid)
        if not ss:
            continue
        schema = ss.get("input_schema")
        props: set[str] = set()
        if isinstance(schema, dict):
            ps = schema.get("properties")
            if isinstance(ps, dict):
                props = set(ps.keys())

        template = data.get("template", "")
        for m in re.finditer(r"walker <project>/submit '([^']*)'", template):
            raw = m.group(1)
            keys = _top_level_keys(raw)
            if not keys:
                continue
            for k in keys:
                if k not in props:
                    violations.append(f"{pid}: '{k}' not declared in input_schema.properties")

    # Diagnostic only: report, do not fail.
    print(f"\n[d] prompt/submit vs input_schema: {len(violations)} violations")
    for v in violations:
        print("  " + v)
