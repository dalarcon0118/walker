"""infrastructure/graph_repository.py — WorkflowGraph repository.

Loads workflow.json (with modular import resolution) and constructs
the domain WorkflowGraph entity. SSOT for workflow definition parsing.

Original: ~200 LOC in repositories.py.
Simplified: ~150 LOC. No ContextResolver, no ValidationService.verify_graph.
Keeps: _resolve_imports + _build_graph (required by real workflow.json).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from ..domain.entities import (
    Gate,
    SubState,
    WorkflowGraph,
    WorkflowNode,
)
from ..domain.enums import ExecutionType
from ..domain.values import RetryPolicy, SelectOption, SideEffectDef

logger = logging.getLogger(__name__)

# Default de sub_state.timeout_ms cuando workflow.json no lo declara (ms).
# Valor idéntico al default legacy — nombrado para eliminar el número mágico.
DEFAULT_SUB_STATE_TIMEOUT_MS = 120_000


class GraphRepository:
    """Load WorkflowGraph from workflow.json.

    SRP: single responsibility = parse JSON → WorkflowGraph.
    The workflow_path is resolved by composition.py (not by this repo).

    Raises FileNotFoundError if workflow.json does not exist.
    Raises json.JSONDecodeError if workflow.json is malformed.
    """

    def __init__(self, workflow_path: str):
        if not workflow_path:
            raise ValueError("workflow_path is required")
        self._workflow_path = workflow_path

    def load(self) -> WorkflowGraph:
        """Load and parse workflow.json into WorkflowGraph.

        1. Read JSON
        2. Resolve imports (modular format)
        3. Build entities (SubState, Gate, WorkflowNode)
        4. Validate invariants
        5. Return WorkflowGraph
        """
        with open(self._workflow_path) as f:
            raw = json.load(f)

        # Resolve modular imports (nodes/edges in separate files)
        if "import" in raw and "nodes" not in raw:
            raw = self._resolve_imports(raw, os.path.dirname(self._workflow_path))

        graph = self._build_graph(raw)

        # Validate invariants (catch broken workflow.json early)
        errors = graph.validate_invariants()
        if errors:
            for err in errors:
                logger.warning("[graph] %s", err)

        return graph

    def _resolve_imports(self, raw: dict, base_dir: str) -> dict:
        """Resolve modular import format: load node/edge files from disk."""
        result = dict(raw)
        result["nodes"] = {}
        result["edges"] = []
        imp = raw.get("import", {})

        # Load each node file
        for node_path in imp.get("nodes", []):
            full_path = os.path.join(base_dir, node_path)
            if not os.path.exists(full_path):
                logger.warning("Node file not found: %s", full_path)
                continue
            with open(full_path) as f:
                node_data = json.load(f)
            # Generate key from filename (e.g. sprint.node.json → SPRINT)
            node_key = os.path.basename(node_path).replace(".node.json", "").upper()
            result["nodes"][node_key] = node_data

        # Load edges
        edges_path = imp.get("edges")
        if edges_path:
            full_edges = os.path.join(base_dir, edges_path)
            if os.path.exists(full_edges):
                with open(full_edges) as f:
                    result["edges"] = json.load(f)

        # Propagate context_bag
        if "context_bag" in raw:
            result["context_bag"] = raw["context_bag"]

        result.pop("import", None)
        return result

    def _build_graph(self, raw: dict) -> WorkflowGraph:
        """Construct WorkflowGraph from parsed JSON."""
        nodes_dict: dict[str, WorkflowNode] = {}

        for node_id, node_raw in raw.get("nodes", {}).items():
            sub_states = []
            for i, ss_raw in enumerate(node_raw.get("sub_states", [])):
                sub_states.append(self._build_sub_state(ss_raw, node_id, i))

            nodes_dict[node_id] = WorkflowNode(
                node_id=node_id,
                sub_states=sub_states,
                label=node_raw.get("label", ""),
                description=node_raw.get("description", ""),
                provides=node_raw.get("provides", []),
            )

        # Build edges
        all_edges: list[Gate] = []
        edges_by_source: dict[str, list[Gate]] = {}
        for e_raw in raw.get("edges", []):
            source = e_raw.get("from", "")
            transform_raw = e_raw.get("transform")
            gate = Gate(
                name=e_raw.get("gate", ""),
                source_node=source,
                target_node=e_raw.get("to", ""),
                target_sub_state=e_raw.get("sub_state_gate", e_raw.get("to_sub")),
                source_sub_state=e_raw.get("from_sub", e_raw.get("sub_state_source")),
                description=e_raw.get("description", ""),
                contract=e_raw.get("contract"),
                transform=dict(transform_raw) if transform_raw else None,
                context_updates=e_raw.get("context_updates"),
            )
            all_edges.append(gate)
            edges_by_source.setdefault(source, []).append(gate)

        return WorkflowGraph(
            nodes=nodes_dict,
            edges=all_edges,
            edges_by_source=edges_by_source,
            initial_state=raw.get("initial_state", ""),
            on_events=dict(raw.get("on", {})),
        )

    def _build_sub_state(self, ss_raw: dict, node_id: str, index: int) -> SubState:
        """Construct a SubState from JSON dict."""
        exec_raw = ss_raw.get("execution", ss_raw.get("mode", ExecutionType.MANUAL.value))

        side_effects = []
        for se_raw in ss_raw.get("side_effects", []):
            if isinstance(se_raw, dict):
                comp_raw = se_raw.get("compensation")
                if isinstance(comp_raw, dict):
                    comp_raw = comp_raw.get("type", "")
                side_effects.append(SideEffectDef(
                    type=se_raw.get("type", ""),
                    params=dict(se_raw.get("params", {})),
                    optional=bool(se_raw.get("optional", False)),
                    compensation=str(comp_raw or ""),
                    idempotency_key=se_raw.get("idempotency_key", ""),
                ))

        retry = None
        retry_raw = ss_raw.get("retry")
        if retry_raw and isinstance(retry_raw, dict):
            retry = RetryPolicy(
                max_attempts=int(retry_raw.get("max_attempts", 3)),
                backoff=str(retry_raw.get("backoff", "exponential")),
                initial_delay_ms=int(retry_raw.get("initial_delay_ms", 1000)),
            )

        options = []
        for opt_raw in ss_raw.get("options", []):
            if isinstance(opt_raw, dict):
                options.append(SelectOption(
                    value=str(opt_raw.get("value", "")),
                    label=str(opt_raw.get("label", "")),
                    description=str(opt_raw.get("description", "")),
                    requires=list(opt_raw.get("requires", [])),
                ))

        ss_key = ss_raw.get("key", f"STEP_{index}")

        return SubState(
            key=ss_key,
            label=ss_raw.get("label", ""),
            execution=exec_raw,
            action=ss_raw.get("action", ""),
            input_schema=dict(ss_raw.get("input_schema", {})),
            output_schema=dict(ss_raw.get("output_schema", {})),
            side_effects=side_effects,
            on_success=ss_raw.get("on_success", ""),
            on_failure=ss_raw.get("on_failure", ""),
            retry=retry,
            timeout_ms=int(ss_raw.get("timeout_ms", DEFAULT_SUB_STATE_TIMEOUT_MS)),
            required_fields=ss_raw.get("required_fields", ss_raw.get("required", [])),
            auto_complete_script=ss_raw.get("auto_complete", ""),
            prompt=ss_raw.get("prompt", ""),
            options=options,
            on_entry_message=ss_raw.get("on_entry_message", ""),
            prompt_id=ss_raw.get("prompt_id", ""),
            context=ss_raw.get("context"),
            required_tools=ss_raw.get("required_tools", []),
            provides=ss_raw.get("provides", []),
            needs=ss_raw.get("needs", []),
            interrupting=bool(ss_raw.get("interrupting", True)),
            on_timeout=ss_raw.get("on_timeout", ""),
            wait_for=list(ss_raw.get("wait_for", [])),
            emits=list(ss_raw.get("emits", [])),
            waits_for=list(ss_raw.get("waits_for", [])),
            on_signal=ss_raw.get("on_signal", ""),
        )
