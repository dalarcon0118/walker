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
    violations: list[str] = []
    # Un ref `NODE.<KEY>` puede apuntar a un sub-estado de OTRO nodo (verify
    # referencia IMPLEMENT.REPORT_GATE). Se resuelve primero en el archivo y,
    # si no está, en el índice global; si la clave existe en varios nodos, se
    # acepta cuando el campo está declarado en alguno (el runtime resuelve por
    # el historial de ejecuciones).
    global_idx: dict[str, list[dict]] = {}
    for p in sorted(NODES_DIR.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        for ss in data.get("sub_states", []) or []:
            key = str(ss.get("key") or "")
            if key:
                global_idx.setdefault(key, []).append(ss)

    for p in sorted(NODES_DIR.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        local = {
            str(ss.get("key")): ss for ss in (data.get("sub_states") or [])
            if ss.get("key")
        }
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
                    candidates = [local[key]] if key in local else global_idx.get(key, [])
                    if not candidates:
                        violations.append(f"{ref}: no sub_state '{key}'")
                        continue
                    props: set[str] = set()
                    for target in candidates:
                        for schema_name in ("input_schema", "output_schema"):
                            schema = target.get(schema_name)
                            if isinstance(schema, dict):
                                ps = schema.get("properties")
                                if isinstance(ps, dict):
                                    props.update(ps.keys())
                    if field and field not in props:
                        violations.append(
                            f"{ref}: field '{field}' not declared in any "
                            f"'{key}' input/output schema"
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
def test_prompt_submit_keys_declared():
    """Todo campo que un prompt manda en un submit está declarado (P0.3b).

    Es bloqueante: un prompt que envía `decision`/`evidence`/`feedback` a un
    sub-estado cuyo schema no los declara hace que la prosa y el contrato
    discrepen, y el gate de accept termina cruzando con campos fantasma.
    """
    submap = _load_nodes()
    pid_to_ss: dict = {}
    # Por prompt_id, desde TODOS los sub-estados: `_load_nodes` colapsa claves
    # homónimas (REVIEW existe en sprint y project_init) y dejaba prompts sin
    # validar. Un prompt sin schema declarado no puede pasar desapercibido.
    for p in sorted(NODES_DIR.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        for ss in data.get("sub_states", []) or []:
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
        # Un campo puede estar declarado como entrada del paso o como campo
        # capturado en el output (el feedback de un gate es el caso típico).
        props: set[str] = set()
        for schema_name in ("input_schema", "output_schema"):
            schema = ss.get(schema_name)
            if isinstance(schema, dict):
                ps = schema.get("properties")
                if isinstance(ps, dict):
                    props.update(ps.keys())

        template = data.get("template", "")
        for m in re.finditer(r"walker <project>/submit '([^']*)'", template):
            raw = m.group(1)
            keys = _top_level_keys(raw)
            if not keys:
                continue
            for k in keys:
                if k not in props:
                    violations.append(f"{pid}: '{k}' not declared in input_schema.properties")

    # Dedupe (el mismo campo aparece en varios ejemplos del mismo prompt).
    violations = sorted(set(violations))
    assert not violations, (
        "prompts que envían campos no declarados:\n  " + "\n  ".join(violations)
    )


# ── node_tasks: el registro de tasks llega al paso (SPEC-003) ─────────────

class TestNodeTasks:
    """Cada nodo declara qué tareas ejecuta; eso tiene que ser visible.

    Antes el registro existía (39 handlers) y ninguna superficie lo mostraba:
    `workflow_state` no listaba tareas y `collect_available_tasks` devolvía []
    porque llamaba a un método inexistente.
    """

    @staticmethod
    def _graph():
        from fast_mcp.features.workflow.infrastructure.graph_repository import (
            GraphRepository,
        )

        return GraphRepository(str(ROOT / "data" / "workflow" / "workflow.json")).load()

    @staticmethod
    def _registry():
        from fast_mcp.features.tasks.domain.task_registry import TaskRegistry

        registry = TaskRegistry()
        registry.register("run_tests", lambda ctx, data: None, {"description": "Run tests"})
        return registry

    def test_verify_declara_sus_tareas(self):
        from fast_mcp.features.workflow.application.orchestrator import node_tasks

        tasks = node_tasks(self._graph().get_node("VERIFY"), self._registry())

        actions = {t["action"] for t in tasks}
        assert actions == {"run_tests", "verify_predicates", "diff_baseline", "monitoring"}

    def test_analyze_expone_la_actualizacion_del_cpg(self):
        from fast_mcp.features.workflow.application.orchestrator import node_tasks

        tasks = node_tasks(self._graph().get_node("ANALYZE"), self._registry())

        assert "sync_code_index" in {t["action"] for t in tasks}

    def test_una_accion_sin_handler_se_ve_antes_de_fallar(self):
        from fast_mcp.features.workflow.application.orchestrator import node_tasks

        tasks = node_tasks(self._graph().get_node("VERIFY"), self._registry())
        by_action = {t["action"]: t for t in tasks}

        assert by_action["run_tests"]["registered"] is True
        assert by_action["run_tests"]["description"] == "Run tests"
        assert by_action["verify_predicates"]["registered"] is False

    def test_sin_nodo_no_inventa_tareas(self):
        from fast_mcp.features.workflow.application.orchestrator import node_tasks

        assert node_tasks(None, self._registry()) == []


def test_los_edges_no_prometen_trabajo_que_el_motor_no_ejecuta():
    """`transform` y `context_updates` de los edges se parsean y nadie los corre.

    El motor sólo ejecuta `sub_state.action` (submit/advance). Medido
    2026-11-26: los 4 transforms apuntaban a acciones y salidas que nadie
    consume — 3 de esas acciones no existían ni en el registro
    (`prepare_analysis_context`, `build_task_briefing`,
    `build_verification_context`) — y `IMPLEMENT.context.briefing`,
    `ANALYZE.context.design` y `VERIFY.context` no tenían lector. Se borraron
    del manifiesto: un edge no puede declarar trabajo que el motor no hace.
    """
    edges = json.loads((ROOT / "data" / "workflow" / "edges.json").read_text())

    promesas = [
        f"{edge.get('from')}->{edge.get('to')}: {sorted(set(edge) & {'transform', 'context_updates'})}"
        for edge in edges
        if "transform" in edge or "context_updates" in edge
    ]

    assert not promesas, (
        "edges declaran trabajo que el motor no ejecuta (no hay call site):\n  "
        + "\n  ".join(promesas)
    )


#: Gates que pueden salir del nodo dejando acciones sin ejecutar. Es una
#: decisión de producto, no una heurística por nombre: si cambia, se cambia acá
#: y el manifiesto a la vez (test de drift).
_ABANDON_GATES = {
    "back_to_active", "back_to_start", "cancel", "close_cancelled",
    "design_needs_refine", "edit_plan", "escalation", "goal_invalidation",
    "needs_refine", "plan_approval_revised", "refine_task", "refresh",
    "reroute_analyze", "reroute_design", "reroute_implement", "reroute_sprint",
    "reroute_verify", "review_needs_work", "revise_specs",
    "spec_review_needs_work",
}


def test_cada_gate_declara_si_avanza_o_abandona():
    """Sin `intent` el motor no puede saber si salir con trabajo pendiente es
    legal. Default implícito = advance: un gate sin clasificar bloquea."""
    edges = json.loads((ROOT / "data" / "workflow" / "edges.json").read_text())

    sin_intencion = [e.get("gate") for e in edges if e.get("intent") not in ("advance", "abandon")]
    assert not sin_intencion, f"gates sin intent: {sin_intencion}"

    abandon = {e["gate"] for e in edges if e["intent"] == "abandon"}
    assert abandon == _ABANDON_GATES, (
        f"drift en gates de abandono: sobran={sorted(abandon - _ABANDON_GATES)} "
        f"faltan={sorted(_ABANDON_GATES - abandon)}"
    )
