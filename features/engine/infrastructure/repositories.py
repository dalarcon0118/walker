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
    WorkflowState,
)


# ── Workflow Repository ────────────────────────────────────────────────────


class WorkflowRepository:
    """Carga el grafo de workflow desde workflow.json.

    SRP: Unica responsabilidad = cargar y construir WorkflowGraph.
    SSOT: El workflow.json es la fuente unica de verdad para la definicion.
    """

    def load(self, chain_dir: str) -> WorkflowGraph:
        """Carga el workflow del motor (SSOT): proyecto o plantilla canónica.

        PRINCIPIO: NO hay fallback hardcoded ni WorkflowGraph() vacío.
        Si no hay workflow, ERROR — el problema debe ser visible.

        Incluye validación de provides/needs (context_resolver).
        Los errores de validación se registran como warning pero
        no bloquean la carga (backwards compatible).

        Raises:
            FileNotFoundError: si no hay workflow del proyecto ni plantilla del motor.
        """
        path = self._resolve_path(chain_dir)
        if not path or not os.path.exists(path):
            from libs.path_resolver import get_engine_workflow_path
            engine_wf = str(get_engine_workflow_path())
            if os.path.exists(engine_wf):
                path = engine_wf
            else:
                raise FileNotFoundError(
                    f"No se encontró workflow para chain_dir='{chain_dir}' "
                    f"ni plantilla canónica del motor en '{engine_wf}'."
                )

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

        # ── Verificación estructural del workflow (teoría de grafos) ──
        try:
            from ..domain.services import ValidationService

            report = ValidationService.verify_graph(graph)
            import logging
            log = logging.getLogger(__name__)
            for err in report.get("errors", []):
                log.error("[graph] %s", err)
            for warn in report.get("warnings", []):
                log.warning("[graph] %s", warn)
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

        Orden de preferencia (SSOT workflow.v3 del motor):
          1. workflow.v3/workflow.json del proyecto (relativo al chain_dir)
          2. workflow.v3/workflow.json canónico del proyecto (project_dir)
          3. plantilla workflow.v3 canónica del motor (get_engine_workflow_path)
          4. None — el caller decide (ERROR, sin fallback hardcoded)

        El workflow legacy inline (workflow.json v4/v5 hardcoded) NO existe
        como opción: es un fallback silencioso que escondía el problema.
        """
        if not chain_dir:
            return None
        parent = os.path.dirname(os.path.dirname(chain_dir))

        # 1. workflow.v3 del proyecto (formato modular, relativo al chain_dir)
        wf_dir = cfg("workflow_dir")
        modular = os.path.join(parent, wf_dir, "workflow.json")
        if os.path.exists(modular):
            return modular

        # 2. workflow.v3 canónico del proyecto (derivado del project_dir)
        canonical = self._canonical_workflow_path(chain_dir)
        if canonical and os.path.exists(canonical):
            return canonical

        # 3. Plantilla canónica del motor (SSOT)
        from libs.path_resolver import get_engine_workflow_path
        engine_wf = str(get_engine_workflow_path())
        if os.path.exists(engine_wf):
            return engine_wf

        return None

    @staticmethod
    def _canonical_workflow_path(chain_dir: str = "") -> str | None:
        """Retorna la ruta al workflow canonico usando project_id derivado del chain_dir."""
        try:
            from features.project import ProjectService

            paths = ProjectService.resolve_paths(chain_dir)
            project_dir = paths.project_root
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
                        on_error=dict(retry_raw.get("on_error", {})) or None,
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

                ss_key = ss_raw.get("key", f"STEP_{i}")
                on_success = ss_raw.get("on_success", "")
                on_failure = ss_raw.get("on_failure", "")

                # ── Validar on_success / on_failure apunten a sub-estados existentes ──
                sub_state_keys = [
                    s.get("key") for s in node_raw.get("sub_states", [])
                ]
                if on_success and on_success not in sub_state_keys:
                    import logging
                    logging.warning(
                        "[%s.%s] on_success='%s' no existe como sub-estado en '%s'",
                        node_id, ss_key, on_success, node_id,
                    )
                if on_failure and on_failure not in sub_state_keys:
                    import logging
                    logging.warning(
                        "[%s.%s] on_failure='%s' no existe como sub-estado en '%s'",
                        node_id, ss_key, on_failure, node_id,
                    )

                sub_states.append(
                    SubState(
                        key=ss_key,
                        label=ss_raw.get("label", ""),
                        execution=execution,
                        action=ss_raw.get("action", ""),
                        input_schema=dict(ss_raw.get("input_schema", {})),
                        output_schema=dict(ss_raw.get("output_schema", {})),
                        side_effects=side_effects,
                        on_success=on_success,
                        on_failure=on_failure,
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

            nodes_dict[node_id] = WorkflowNode(
                node_id=node_id,
                sub_states=sub_states,
                label=node_raw.get("label", ""),
                description=node_raw.get("description", ""),
                entry_action_script=node_raw.get("entry_action", ""),
                on_entry_message=node_raw.get("on_entry_message", ""),
                provides=node_raw.get("provides", []),
            )

        # Construir edges como lista plana del grafo (SSOT)
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


# ── WorkflowState Repository ─────────────────────────────────────────────────


class WorkflowStateRepository:
    """Persistencia del estado del workflow (PURO: solo WorkflowState).

    SRP: Unica responsabilidad = load/save de WorkflowState.
    SSOT: .workflow-state.json (nuevo) con fallback a sprint-state.json (legacy).

    PRINCIPIO: No sabe de plan.json, tasks de sprint, ni nada de negocio.
    Eso es responsabilidad de SprintStateRepository en features/spring/.
    """

    STATE_FILE = ".workflow-state.json"
    LEGACY_FILE = "sprint-state.json"

    def load(self, chain_dir: str) -> WorkflowState:
        """Carga WorkflowState. Prueba .workflow-state.json primero,
        luego sprint-state.json (legacy). Si nada, retorna vacío."""
        # Nuevo formato
        path = os.path.join(chain_dir, self.STATE_FILE)
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return WorkflowState.from_dict(json.load(f))
            except (json.JSONDecodeError, OSError):
                pass

        # Legacy: leer sprint-state.json y extraer solo campos workflow
        legacy = os.path.join(chain_dir, self.LEGACY_FILE)
        if os.path.exists(legacy):
            try:
                with open(legacy) as f:
                    raw = json.load(f)
                wf = WorkflowState.from_dict(raw)
                # Si habia datos utiles, migrar al nuevo formato
                if wf.current_node:
                    try:
                        self.save(chain_dir, wf)
                    except Exception:
                        pass
                return wf
            except (json.JSONDecodeError, OSError):
                pass

        return WorkflowState()

    def save(self, chain_dir: str, state: WorkflowState) -> None:
        """Persiste WorkflowState a .workflow-state.json (solo campos engine)."""
        path = os.path.join(chain_dir, self.STATE_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(state.to_dict(), f, indent=2, ensure_ascii=False)


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
