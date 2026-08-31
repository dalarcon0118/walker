"""
infrastructure/repositories.py — Implementacion concreta de repositorios.

SRP: Cada repositorio maneja UN tipo de persistencia.
SSOT: Los formatos de archivo se definen aqui; el dominio no sabe de JSON.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace

# config import moved to composition root
from ..application.context_resolver import ContextResolver
from ..domain.errors import StateConflictError
from ..domain.entities import (
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

    El workflow_path se resuelve FUERA del repo (main/composition root).
    El repo solo abre y parsea el archivo que le pasan.
    """

    def __init__(self, workflow_path: str = ""):
        """Recibe el workflow_path resuelto por el caller.

        Args:
            workflow_path: Ruta absoluta a workflow.json.
                          Si es vacío, busca en config del engine (DEPRECATED).
        """
        if workflow_path:
            self._workflow_path = workflow_path
        else:
            import warnings
            from shared.core.engine.infrastructure.config import get_workflow_manifest_path
            warnings.warn(
                "WorkflowRepository() sin workflow_path: usando fallback a "
                "get_workflow_manifest_path(). El caller debe resolver el path "
                "(main/composition root).",
                DeprecationWarning,
                stacklevel=2,
            )
            self._workflow_path = str(get_workflow_manifest_path())

    def load(self, chain_dir: str | None = None) -> WorkflowGraph:
        """Carga el workflow desde el path configurado en el constructor.

        Args:
            chain_dir: DEPRECATED. No se usa para resolver paths.
                       Se mantiene por backward compatibility con callers existentes.

        Raises:
            FileNotFoundError: si el workflow_path no existe.
        """
        path = self._workflow_path
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"No se encontró workflow.json en '{path}'. "
                "El main/composition root debe resolver y pasar el workflow_path."
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

        # Verificación estructural del workflow (teoría de grafos)
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
                        comp_raw = se_raw.get("compensation")
                        if isinstance(comp_raw, dict):
                            comp_raw = comp_raw.get("type", "")
                        side_effects.append(
                            SideEffectDef(
                                type=se_raw.get("type", ""),
                                params=dict(se_raw.get("params", {})),
                                optional=bool(se_raw.get("optional", False)),
                                compensation=str(comp_raw or ""),
                                idempotency_key=se_raw.get("idempotency_key", ""),
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

                # Validar on_success / on_failure apunten a sub-estados existentes
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
                        execution=exec_raw,
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

        graph = WorkflowGraph(
            nodes=nodes_dict,
            edges=all_edges,
            edges_by_source=edges_by_source,
            initial_state=raw.get("initial_state", ""),
            on_events=dict(raw.get("on", {})),
        )
        errors = graph.validate_invariants()
        if errors:
            import logging
            logging.getLogger(__name__).warning(
                "WorkflowGraph invariants failed: %s", errors
            )
        return graph


class WorkflowStateRepository:
    """Persistencia del estado del workflow (PURO: solo WorkflowState).

    SRP: Unica responsabilidad = load/save de WorkflowState.
    SSOT: .workflow-state.json es el UNICO estado del motor.

    PRINCIPIO estructural: .workflow-state.json (motor SAGA) y el DeltaGraph
    (negocio spring) son las dos unicas fuentes. sprint-state.json fue
    ELIMINADO — no hay fallback legacy.

    PRINCIPIO: No sabe de plan.json, tasks de sprint, ni nada de negocio.
    Eso es responsabilidad del SpringService en features/delta_tracking/.
    """

    STATE_FILE = ".workflow-state.json"

    def load(self, chain_dir: str) -> WorkflowState:
        """Carga WorkflowState desde .workflow-state.json.

        Incluye reconciliación por invariante (GAP-1): al cargar se valida
        que sub_state_index ↔ current_sub_state sean coherentes con el nodo
        actual del workflow. Si el archivo quedó desfasado (p.ej. al pausar
        la especificación para editar el backlog), el índice se corrige a
        partir del sub-estado real, evitando que las gates queden bloqueadas.

        Patrón Result (returns): los fallos de lectura se loggean y se
        degradan a estado vacío; nunca se silencian ni impiden la carga.

        Si no existe, retorna vacío (el estado se crea al iniciar el sprint).
        """
        import logging

        log = logging.getLogger(__name__)
        from returns.result import safe
        from returns.pipeline import is_successful

        @safe
        def _read() -> WorkflowState:
            path = os.path.join(chain_dir, self.STATE_FILE)
            with open(path) as f:
                return WorkflowState.from_dict(json.load(f))

        read_result = _read()
        if not is_successful(read_result):
            log.error("WorkflowState corrupto o ilegible: %s", read_result.failure())
            return WorkflowState()
        state = read_result.value_or(WorkflowState())
        if not state.current_node:
            return state

        @safe
        def _reconcile() -> tuple[WorkflowState, list[str]]:
            from shared.core.engine.infrastructure.config import get_workflow_manifest_path
            wf_repo = WorkflowRepository(workflow_path=str(get_workflow_manifest_path()))
            graph = wf_repo.load()
            return state.reconcile_index(graph)

        rec_result = _reconcile()
        if is_successful(rec_result):
            reconciled, warnings = rec_result.value_or((state, []))
            for w in warnings:
                log.warning("%s", w)
            return reconciled
        # Reconciliación best-effort: nunca impide la carga del estado.
        log.error("Reconciliación de estado falló (se usa estado crudo): %s", rec_result.failure())
        return state

    def save(self, chain_dir: str, state: WorkflowState) -> None:
        """Persiste WorkflowState a .workflow-state.json (solo campos engine).

        UNIT-OF-WORK (ADR-WALKER-067 F6/G): optimistic concurrency vía
        `state.revision` (CAS). Si la revisión del estado entrante no coincide
        con la persistida, hay un lost update → StateConflictError.
        """
        path = os.path.join(chain_dir, self.STATE_FILE)
        stored_revision = 0
        if os.path.exists(path):
            try:
                with open(path) as f:
                    stored_revision = int((json.load(f).get("revision") or 0))
            except Exception:
                stored_revision = 0
        if state.revision != stored_revision:
            raise StateConflictError(chain_dir, state.revision, stored_revision)
        new_state = replace(state, revision=stored_revision + 1)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(new_state.to_dict(), f, indent=2, ensure_ascii=False)
        # CAS: retorna el estado persistido (nueva revisión) para que el
        # pipeline encadene saves sin conflicto (ADR-068 F6/G).
        return new_state


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

    def run_script(
        self,
        script: str,
        chain_dir: str | None = None,
        task_id: str | None = None,
    ) -> dict:
        """Implementación del puerto IWorkflowIO.run_script (DIP, Fase 2).

        Delega en ``run`` para reutilizar el reemplazo de placeholders y el
        manejo de timeouts. Contrato de retorno: {"exit_code", "stdout", "stderr"}.
        """
        return self.run(script, chain_dir=chain_dir, task_id=task_id)
