"""
infrastructure/repositories.py — Implementacion concreta de repositorios.

SRP: Cada repositorio maneja UN tipo de persistencia.
SSOT: Los formatos de archivo se definen aqui; el dominio no sabe de JSON.
"""

from __future__ import annotations

import json
import os
import subprocess

from .config import get as cfg
from ..application.context_resolver import ContextResolver
from ..domain.models import (
    WorkflowGraph,
    WorkflowNode,
    SubState,
    ExecutionType,
    RetryPolicy,
    SideEffectDef,
    SelectOption,
    Gate,
    SprintState,
)


# ── Workflow Repository ────────────────────────────────────────────────────


class WorkflowRepository:
    """Carga el grafo de workflow desde workflow.json.

    SRP: Unica responsabilidad = cargar y construir WorkflowGraph.
    SSOT: El workflow.json es la fuente unica de verdad para la definicion.
    """

    def load(self, chain_dir: str) -> WorkflowGraph:
        """Carga el workflow desde el projecto asociado al chain_dir.

        Incluye validación de provides/needs (context_resolver).
        Los errores de validación se registran como warning pero
        no bloquean la carga (backwards compatible).
        """
        path = self._resolve_path(chain_dir)
        if not path or not os.path.exists(path):
            return WorkflowGraph()  # Grafo vacio como fallback

        with open(path) as f:
            raw = json.load(f)

        # Si tiene 'import', resolver archivos externos
        if "import" in raw and "nodes" not in raw:
            raw = self._resolve_imports(raw, os.path.dirname(path))

        graph = self._build_graph(raw)

        # Validar provides/needs (no bloqueante)
        try:
            resolver = ContextResolver()
            errors = resolver.validate_graph(graph)
            if errors:
                import logging

                log = logging.getLogger(__name__)
                for err in errors:
                    log.warning("[context] %s", err)
        except Exception:
            pass  # Non-blocking

        return graph

    def _resolve_imports(self, raw: dict, base_dir: str) -> dict:
        """Resuelve imports del formato modular: carga archivos de nodo y edges."""
        result = dict(raw)
        result["nodes"] = {}
        result["edges"] = []
        imp = raw.get("import", {})

        # Cargar cada archivo de nodo
        for node_path in imp.get("nodes", []):
            full_path = os.path.join(base_dir, node_path)
            if not os.path.exists(full_path):
                continue
            with open(full_path) as f:
                node_data = json.load(f)
            # Generar key desde nombre de archivo (e.g. sprint.node.json -> SPRINT)
            node_key = os.path.basename(node_path).replace(".node.json", "").upper()
            result["nodes"][node_key] = node_data

        # Cargar edges
        edges_path = imp.get("edges")
        if edges_path:
            full_edges = os.path.join(base_dir, edges_path)
            if os.path.exists(full_edges):
                with open(full_edges) as f:
                    result["edges"] = json.load(f)

        # Propagar context_bag
        if "context_bag" in raw:
            result["context_bag"] = raw["context_bag"]

        result.pop("import", None)
        return result

    def _resolve_path(self, chain_dir: str) -> str | None:
        """Resuelve la ruta al workflow desde un chain_dir.

        Orden de preferencia:
          1. workflow.v3/workflow.json relativo al chain_dir
          2. workflow.json (formato legacy inline)
          3. Session dir → workflow canonico (derivado del chain_dir)
          4. Fallback canonico derivado del chain_dir
        """
        if not chain_dir:
            return None
        parent = os.path.dirname(os.path.dirname(chain_dir))

        # 1. Nuevo formato modular
        wf_dir = cfg("workflow_dir")
        modular = os.path.join(parent, wf_dir, "workflow.json")
        if os.path.exists(modular):
            return modular

        # 2. Legacy inline
        candidate = os.path.join(parent, "workflow.json")
        if os.path.exists(candidate):
            return candidate

        # 3. Session dir → workflow canonico (derivado)
        if ".walker/sessions" in chain_dir or ".cognitive/sessions" in chain_dir:
            canonical = self._canonical_workflow_path(chain_dir)
            if canonical and os.path.exists(canonical):
                return canonical

        # 4. Fallback canonico derivado
        canonical = self._canonical_workflow_path(chain_dir)
        if canonical and os.path.exists(canonical):
            return canonical

        return None

    @staticmethod
    def _canonical_workflow_path(chain_dir: str = "") -> str | None:
        """Retorna la ruta al workflow canonico usando project_id derivado del chain_dir."""
        try:
            from _walker.shared.path_resolver import resolve_project_root

            project_dir = resolve_project_root(chain_dir)
            if not project_dir or not os.path.isdir(project_dir):
                root = cfg("repo_root")
                if not root:
                    return None
                project_dir = root
            wf_dir = cfg("workflow_dir")
            return os.path.join(project_dir, wf_dir, "workflow.json")
        except Exception:
            return None

    def _build_graph(self, raw: dict) -> WorkflowGraph:
        """Construye WorkflowGraph desde el JSON crudo."""
        nodes_dict: dict[str, WorkflowNode] = {}

        # Construir nodos
        for node_id, node_raw in raw.get("nodes", {}).items():
            sub_states = []
            for i, ss_raw in enumerate(node_raw.get("sub_states", [])):
                # v3: leer execution (con fallback a mode legacy)
                exec_raw = ss_raw.get("execution", ss_raw.get("mode", "manual"))
                execution = ExecutionType(exec_raw)

                # Parse side_effects
                side_effects = []
                for se_raw in ss_raw.get("side_effects", []):
                    if isinstance(se_raw, dict):
                        side_effects.append(
                            SideEffectDef(
                                type=se_raw.get("type", ""),
                                params=dict(se_raw.get("params", {})),
                                optional=bool(se_raw.get("optional", False)),
                            )
                        )

                # Parse retry
                retry_raw = ss_raw.get("retry")
                retry = None
                if retry_raw and isinstance(retry_raw, dict):
                    retry = RetryPolicy(
                        max_attempts=int(retry_raw.get("max_attempts", 3)),
                        backoff=str(retry_raw.get("backoff", "exponential")),
                        initial_delay_ms=int(retry_raw.get("initial_delay_ms", 1000)),
                    )

                # Parse options
                options = []
                for opt_raw in ss_raw.get("options", []):
                    if isinstance(opt_raw, dict):
                        options.append(
                            SelectOption(
                                value=str(opt_raw.get("value", "")),
                                label=str(opt_raw.get("label", "")),
                                description=str(opt_raw.get("description", "")),
                                requires=list(opt_raw.get("requires", [])),
                            )
                        )

                sub_states.append(
                    SubState(
                        key=ss_raw.get("key", f"STEP_{i}"),
                        label=ss_raw.get("label", ""),
                        execution=execution,
                        action=ss_raw.get("action", ""),
                        input_schema=dict(ss_raw.get("input_schema", {})),
                        output_schema=dict(ss_raw.get("output_schema", {})),
                        side_effects=side_effects,
                        on_success=ss_raw.get("on_success", ""),
                        on_failure=ss_raw.get("on_failure", ""),
                        retry=retry,
                        timeout_ms=int(ss_raw.get("timeout_ms", 120000)),
                        required_fields=ss_raw.get(
                            "required_fields", ss_raw.get("required", [])
                        ),
                        auto_complete_script=ss_raw.get("auto_complete", ""),
                        on_failure_loop=bool(ss_raw.get("on_failure_loop", False)),
                        order=i,
                        prompt=ss_raw.get("prompt", ""),
                        options=options,
                        on_entry_message=ss_raw.get("on_entry_message", ""),
                        prompt_id=ss_raw.get("prompt_id", ""),
                        context=ss_raw.get("context"),
                        provides=ss_raw.get("provides", []),
                        needs=ss_raw.get("needs", []),
                    )
                )

            gates = []
            for e_raw in raw.get("edges", []):
                if e_raw.get("from") == node_id:
                    transform_raw = e_raw.get("transform")
                    gates.append(
                        Gate(
                            name=e_raw.get("gate", ""),
                            source_node=node_id,
                            target_node=e_raw.get("to", ""),
                            target_sub_state=e_raw.get("sub_state_gate"),
                            description=e_raw.get("description", ""),
                            transform=dict(transform_raw) if transform_raw else None,
                            context_updates=e_raw.get("context_updates"),
                        )
                    )

            nodes_dict[node_id] = WorkflowNode(
                node_id=node_id,
                sub_states=sub_states,
                gates=gates,
                label=node_raw.get("label", ""),
                description=node_raw.get("description", ""),
                entry_action_script=node_raw.get("entry_action", ""),
                on_entry_message=node_raw.get("on_entry_message", ""),
                provides=node_raw.get("provides", []),
            )

        return WorkflowGraph(
            nodes=nodes_dict,
            initial_state=raw.get("initial_state", ""),
            on_events=dict(raw.get("on", {})),
        )


# ── SprintState Repository ─────────────────────────────────────────────────


class SprintStateRepository:
    """Persistencia del estado del sprint.

    SRP: Unica responsabilidad = load/save de SprintState.
    SSOT: sprint-state.json es la fuente unica de verdad.
    """

    STATE_FILE = "sprint-state.json"

    def load(self, chain_dir: str) -> SprintState:
        """Carga SprintState desde el archivo."""
        path = os.path.join(chain_dir, self.STATE_FILE)
        if not os.path.exists(path):
            return SprintState()

        with open(path) as f:
            raw = json.load(f)
        return SprintState.from_dict(raw)

    def save(self, chain_dir: str, state: SprintState) -> None:
        """Persiste SprintState a archivo y sincroniza plan.json."""
        path = os.path.join(chain_dir, self.STATE_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = state.to_dict()
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        # ── Sync plan.json con task statuses del sprint-state ────────────
        plan_path = os.path.join(chain_dir, "plan.json")
        if os.path.exists(plan_path):
            try:
                with open(plan_path) as f:
                    plan = json.load(f)
                plan_tasks = (
                    plan.get("tasks")
                    if isinstance(plan.get("tasks"), list)
                    else plan.get("plan", {}).get("tasks", [])
                )
                state_tasks = data.get("tasks", [])
                # Build a lookup from state tasks (list of dicts with task_id)
                state_status = {}
                if isinstance(state_tasks, list):
                    for st in state_tasks:
                        tid = st.get("task_id", "") or st.get("id", "")
                        if tid:
                            state_status[tid] = st.get("status", "pending")
                elif isinstance(state_tasks, dict):
                    for tid, st in state_tasks.items():
                        state_status[tid] = (
                            st.get("status", "pending")
                            if isinstance(st, dict)
                            else "pending"
                        )

                if plan_tasks and state_status:
                    for task in plan_tasks:
                        tid = task.get("task_id", "")
                        if tid in state_status:
                            task["status"] = state_status[tid]
                    with open(plan_path, "w") as f:
                        json.dump(plan, f, indent=2, ensure_ascii=False)
            except Exception:
                pass  # Si falla la sync, no bloquear el guardado del state

    def save_snapshot(self, chain_dir: str, state: SprintState, label: str = "") -> str:
        """Guarda un snapshot del estado para backup/recovery."""
        snap_dir = os.path.join(chain_dir, "snapshots")
        os.makedirs(snap_dir, exist_ok=True)
        ts = state.updated_at.replace(":", "-").replace(".", "-")
        suffix = f"_{label}" if label else ""
        path = os.path.join(snap_dir, f"state_{ts}{suffix}.json")
        with open(path, "w") as f:
            json.dump(state.to_dict(), f, indent=2, ensure_ascii=False)
        return path


# ── Script Runner ──────────────────────────────────────────────────────────


class ScriptRunner:
    """Ejecuta scripts externos (auto_complete, gate_validation).

    SRP: Unica responsabilidad = ejecutar comandos y capturar resultado.
    Es la unica clase que hace subprocess.run en este contexto.
    """

    DEFAULT_TIMEOUT = 120

    def run(
        self,
        script: str,
        chain_dir: str | None = None,
        task_id: str | None = None,
        timeout: int | None = None,
    ) -> dict:
        """Ejecuta un script y retorna resultado."""
        if not script:
            return {"exit_code": 0, "stdout": "", "stderr": ""}

        # Reemplazar placeholders
        cmd = script
        if chain_dir:
            cmd = cmd.replace("<chain>", chain_dir).replace("{chain}", chain_dir)
        if task_id:
            cmd = cmd.replace("<task_id>", task_id).replace("{task_id}", task_id)

        timeout = timeout or self.DEFAULT_TIMEOUT

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout or "",
                "stderr": result.stderr or "",
            }
        except subprocess.TimeoutExpired:
            return {"exit_code": -1, "stdout": "", "stderr": "TIMEOUT"}
        except Exception as e:
            return {"exit_code": -1, "stdout": "", "stderr": str(e)}
