"""
application/workflow_api.py — API Surface del Workflow v3.

Capa entre el CLI (walker) y el OrchestrationService.
Gestiona el ciclo de vida de sesión, el bootstrap automático,
y la construcción de ExecutionContext desde session_id.

Flujo:
  1. walker recibe comando → busca session_id activo
  2. Si no hay sesión → ejecuta bootstrap (session_init)
  3. WorkflowAPI.build_context(session_id) → ExecutionContext
  4. Delega en OrchestrationService.{step|submit|transition|status}
"""

from __future__ import annotations

import os
from typing import Any

from ..domain.models import ExecutionContext
from ..domain.session import SessionRecord
from ..domain.ports import ITracer, ISessionStore
from ..domain.ports_registry import resolve_port
from .bundle import EngineBundle
from .orchestrator import OrchestrationService


class WorkflowAPI:
    """API Surface del workflow con manejo de sesión.

    El motor es anémico: NO acopla a ningún service locator, contenedor DI ni
    arquitectura externa. Recibe TODAS sus dependencias de ejecución en un VO
    (EngineBundle) que construye el host (main/app) y se lo inyecta. El motor
    solo consume el VO; no resuelve nada por sí solo.
    """

    def __init__(
        self,
        bundle: EngineBundle,
        session_id: str | None = None,
        chain_dir: str | None = None,
        project_id: str | None = None,
        session_store: Any = None,
        tracer: ITracer | None = None,
        orch: OrchestrationService | None = None,
    ):
        self._bundle = bundle
        self._session_store = session_store or resolve_port(ISessionStore)
        self._session_id = session_id
        self._chain_dir = chain_dir
        self._project_id = project_id
        self._orch: OrchestrationService | None = orch
        self._tracer = tracer
        self.status_message: str = ""
        self.suggestion: str = ""

    # ── Propiedades ───────────────────────────────────────────────

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def chain_dir(self) -> str | None:
        return self._chain_dir

    @property
    def project_id(self) -> str | None:
        return self._project_id

    # ── Bootstrap / Sesión ────────────────────────────────────────

    def _trace_session_start(self, record: Any) -> None:
        """Registra un evento session_start en el TraceGraph.

        Tracing PLUGGABLE (ADR-WALKER-067 F5/I): el motor usa el ITracer
        inyectado (o el default por puerto); NO construye un logger concreto.
        """
        try:
            if self._tracer is None:
                from ..domain.ports_registry import resolve_port
                from ..domain.ports import ITracer as _ITracer
                from ..infrastructure.trace_log import NoopTracer

                self._tracer = resolve_port(_ITracer) or NoopTracer()
            chain_dir = record.chain_dir or ""
            if not chain_dir:
                return
            self._tracer.trace(
                chain_dir,
                "session_start",
                data={
                    "session_id": record.session_id,
                    "project_id": record.project_id or self._project_id or "",
                    "mode": "new",
                },
                session_id=record.session_id,
            )
        except Exception:
            pass  # best-effort

    def ensure_session(self) -> str:
        """Asegura que hay una sesión activa.

        Sets:
            status_message: Descripcion de lo que se encontro o no.
            suggestion: Sugerencia de comando para el usuario.
        """
        self.status_message = ""
        self.suggestion = ""

        # GC periódico de sesiones stale (throttle horario interno): evita
        # que <sessions_dir> vuelva a acumular huérfanas con el tiempo.
        try:
            self._session_store.gc_stale()
        except Exception:
            pass  # best-effort: el GC nunca debe bloquear el flujo

        # 1. Usar session_id explícito si existe
        if self._session_id:
            record = self._session_store.load(self._session_id)
            if record:
                self._chain_dir = record.chain_dir
                self._project_id = record.project_id
                self._session_store.set_active(self._session_id)
                if self._chain_dir:
                    self.status_message = f"Reanudando sesión en {record.project_id}"
                else:
                    self.status_message = "Sesión reanudada (sin proyecto activo)"
                return self._session_id

        # 2. Si hay chain_dir explícito (pasado por el CLI), usarlo siempre
        #    Esto permite que `walker <project>/status` funcione incluso
        #    si hay otra sesión activa (multitenant).
        #    Reuso: si ya existe una sesión para este chain_dir y no está
        #    stale, se reutiliza en vez de crear una nueva (evita llenar
        #    .cognitive/sessions/ con duplicados por cada llamada CLI/MCP).
        if self._chain_dir:
            record = self._session_store.find_by_chain_dir(self._chain_dir)
            if record and not record.is_stale():
                self._session_id = record.session_id
                self._project_id = record.project_id or self._project_id
                self._session_store.set_active(self._session_id)
                self.status_message = (
                    f"Sesión para {self._project_id or os.path.basename(self._chain_dir)}"
                )
                return self._session_id
            record = SessionRecord.create(
                chain_dir=self._chain_dir, project_id=self._project_id
            )
            self._session_id = record.session_id
            self._session_store.save(record)
            self._session_store.set_active(self._session_id)
            self.status_message = (
                f"Sesión para {self._project_id or os.path.basename(self._chain_dir)}"
            )
            self._trace_session_start(record)
            return self._session_id

        # 3. Buscar sesión activa (solo si no hay chain_dir explícito)
        active = self._session_store.get_active()
        if active:
            self._session_id = active.session_id
            self._chain_dir = active.chain_dir
            self._project_id = active.project_id
            if self._chain_dir:
                self.status_message = f"Sesión activa: {active.project_id}"
            else:
                self.status_message = "Sesión activa (sin proyecto)"
            return self._session_id

        # ── BOOTSTRAP: No hay sesión activa ni proyecto ──────────────
        # En vez de auto-asignar el primer proyecto disponible (comportamiento
        # anterior), creamos una sesión bootstrap sin proyecto. El workflow
        # entrará al nodo START (initial_state) que permite al agente elegir.
        #
        # Usamos session_dir como chain_dir
        # virtual para persistir el estado del motor (.workflow-state.json)
        # hasta que se seleccione un proyecto.
        from ..infrastructure.repositories import (
            WorkflowRepository,
            WorkflowStateRepository,
        )

        record = SessionRecord.create()
        record.chain_dir = (
            self._session_store.ensure_session_dir(record)
        )  # ← chain_dir virtual en el record
        self._session_id = record.session_id
        self._chain_dir = record.chain_dir
        self._project_id = ""

        # Inicializar el estado del workflow desde workflow.initial_state
        try:
            # Los repos vienen del VO inyectado por el host (EngineBundle); el
            # motor no resuelve paths ni construye concreto alguno.
            wf_store = self._bundle.workflow_store
            workflow = wf_store.load_graph()
            state = wf_store.load_state(self._chain_dir)

            if workflow.initial_state and not state.current_node:
                # INMUTABILIDAD (ADR-WALKER-067 F2/F): WorkflowState es
                # inmutable; se construye con builders (dataclasses.replace)
                # que preservan revision (CAS) y no mutan el estado previo.
                node = workflow.get_node(workflow.initial_state)
                first_sub = (
                    node.first_sub_state.key
                    if node and node.first_sub_state
                    else "INIT"
                )
                state = (
                    state.with_node(workflow.initial_state, first_sub, index=0)
                    .add_history(f"{workflow.initial_state}.{first_sub}")
                    .with_context("session_id", self._session_id)
                    .with_context("chain_dir", self._chain_dir)
                )
                wf_store.save_state(state, self._chain_dir)
                self.status_message = (
                    f"Bootstrap: nodo '{workflow.initial_state}' inicializado"
                )
            else:
                self.status_message = "Sesión bootstrap (sin proyecto)"
                self.suggestion = "Usa 'walker list' para ver proyectos disponibles"
        except Exception as e:
            self.status_message = f"Sesión bootstrap (error: {e})"

        self._session_store.save(record)
        self._session_store.set_active(self._session_id)
        return self._session_id

    def start(self, project_id: str, goal: str) -> dict:
        """Inicia un nuevo proyecto/sprint desde cero."""
        record = SessionRecord.create(project_id=project_id)
        self._session_id = record.session_id
        self._session_store.save(record)
        self._session_store.set_active(self._session_id)
        return {"session_id": self._session_id, "status": "created"}

    # ── Orchestrator (lazy) ───────────────────────────────────────

    def _get_orch(self) -> OrchestrationService:
        if self._orch is None:
            # El motor no resuelve nada: construye el OrchestrationService DIRECTAMENTE
            # desde el VO que le inyectó el host. Cero acoplamiento a locator/registry.
            self._orch = OrchestrationService(**self._bundle.to_orchestrator_kwargs())
        return self._orch

    def _build_context(self) -> ExecutionContext:
        """Construye ExecutionContext desde la sesión actual.

        Lee task_id del DELTA (nodo spring) si está disponible para que
        _build_prompt_context() pueda identificar la tarea actual.
        PRINCIPIO: el estado de negocio del spring vive en el delta;
        sprint-state.json fue eliminado estructuralmente.
        """
        session_id = self.ensure_session()
        task_id = ""
        if self._chain_dir:
            # El task_id actual (spec del spring) es INCUMBENCIA DEL HOST
            # (ADR-WALKER-067): el engine NO consulta spring. El host provee el
            # task_id vía el contexto; si no, queda vacío.
            try:
                pass
            except Exception:
                pass
        return ExecutionContext(
            session_id=session_id,
            chain_dir=self._chain_dir or "",
            trace_id=session_id,
            span_id="api",
            task_id=task_id,
        )

    # ── API Surface ───────────────────────────────────────────────

    def step(self) -> dict:
        """Evalúa el sub-estado actual y devuelve el siguiente paso."""
        ctx = self._build_context()
        return self._get_orch().step(ctx)

    def submit(self, **fields: str) -> dict:
        """Procesa datos para el sub-estado actual."""
        ctx = self._build_context()
        return self._get_orch().submit(ctx, fields=fields)

    def advance(self) -> dict:
        """Avanza al siguiente sub-estado dentro del mismo nodo.

        Solo funciona si hay un on_success definido en el sub-estado actual.
        Útil para user_input después de presentar resultados al usuario.
        """
        ctx = self._build_context()
        return self._get_orch().advance_sub_state(ctx)

    def transition(
        self, gate: str, reason: str = "", skip_preconditions: bool = False
    ) -> dict:
        """Transiciona al siguiente nodo.

        skip_preconditions=True (GAP-4, analogía git --force): salta las
        precondiciones SOLO con una razón documentada. La decisión de forzar
        queda registrada (el caller debe persistirla en CKG/trace).
        """
        ctx = self._build_context()
        return self._get_orch().transition(
            ctx, gate, reason=reason, skip_preconditions=skip_preconditions
        )

    def status(self) -> dict:
        """Snapshot del estado actual."""
        ctx = self._build_context()
        return self._get_orch().status(ctx)

    def workflow(self) -> dict:
        """Retorna la estructura completa del workflow: todos los nodos,
        sub-estados, descripciones, campos requeridos, y transiciones
        disponibles. El agente usa esto para navegar el workflow."""
        wf_store = self._bundle.workflow_store
        workflow = wf_store.load_graph()
        nodes = {}
        for nid, node in workflow.nodes.items():
            sub_states = []
            for ss in node.sub_states:
                entry = {
                    "key": ss.key,
                    "label": ss.label or ss.key,
                    "execution": ss.execution.value if ss.execution else "manual",
                    "required_fields": list(ss.required_fields),
                    "message": ss.on_entry_message or "",
                }
                if ss.auto_complete_script:
                    entry["auto_complete"] = ss.auto_complete_script
                if ss.input_schema:
                    entry["input_schema"] = ss.input_schema
                if ss.output_schema:
                    entry["output_schema"] = ss.output_schema
                sub_states.append(entry)
            gates = [
                {
                    "name": g.name,
                    "target": g.target_node,
                    "target_sub_state": g.target_sub_state,
                    "description": g.description,
                }
                for g in workflow.edges_by_source.get(nid, [])
            ]
            nodes[nid] = {
                "label": node.label or nid,
                "description": node.description or "",
                "sub_states": sub_states,
                "gates": gates,
            }
        # Event handlers on:{}
        on_events = {}
        for evt_name, evt_cfg in workflow.on_events.items():
            entry = {}
            if evt_cfg.get("run"):
                entry["run"] = evt_cfg["run"]
            if evt_cfg.get("transition"):
                entry["transition"] = evt_cfg["transition"]
            if evt_cfg.get("description"):
                entry["description"] = evt_cfg["description"]
            on_events[evt_name] = entry

        # Auto-generated flows (narrative walkthrough)
        flows = workflow.generate_flows()

        return {
            "version": "4.0",
            "initial_state": workflow.initial_state,
            "nodes": nodes,
            "edges": [
                {
                    "from": g.source_node,
                    "to": g.target_node,
                    "gate": g.name,
                    "target_sub_state": g.target_sub_state,
                    "description": g.description,
                }
                for g in workflow.edges
            ],
            "on": on_events,
            "flows": flows,
            "note": "Usa 'transition <gate>' para navegar entre nodos. "
            "NO ejecutes tasks directamente si existe un nodo de workflow. "
            "Primero descubre el nodo correcto con este comando, "
            "luego navega via transitions. "
            "Los event handlers 'on:' se ejecutan automaticamente por el engine.",
        }

    def session(self, action: str, **params) -> dict:
        """Gestiona ciclo de vida de sesión."""
        if action == "init":
            return self.start(
                project_id=params.get("project_id", ""),
                goal=params.get("goal", ""),
            )
        elif action == "terminate":
            if self._session_id:
                self._session_store.delete(self._session_id)
            return {"status": "terminated"}
        return {"status": "unknown_action"}
