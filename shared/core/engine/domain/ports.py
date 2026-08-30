"""domain/ports.py — Puertos (interfaces) del dominio Workflow Navigation.

PRINCIPIO: El dominio define QUÉ necesita, no CÓMO se implementa.
Los adaptadores de infrastructure implementan estos puertos.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from shared.core.engine.domain.entities import WorkflowGraph, WorkflowState, WorkflowNode


class IWorkflowIO(ABC):
    """Puerto para operaciones de IO del workflow.

    Abstrae:
      - Ejecución de scripts (gate validation, auto_complete)
      - Output a stderr
      - Acceso a sub-estados en workflow dicts
    """

    @abstractmethod
    def run_script(self, script: str, chain_dir: str | None = None, task_id: str | None = None) -> dict[str, Any]:
        """Ejecuta un script shell y retorna resultado."""
        ...

    @abstractmethod
    def eprint(self, *args: Any, **kwargs: Any) -> None:
        """Imprime a stderr."""
        ...

    @abstractmethod
    def get_sub_state(
        self, workflow: WorkflowGraph, node_id: str, sub_key: str
    ) -> Any | None:
        """Obtiene un sub-estado del grafo del workflow (tipado)."""
        ...




class IProjectContext(ABC):
    """Puerto para acceso al contexto del proyecto (paths, metadata).

    El engine NO conoce features.project. Solo necesita resolver rutas
    del proyecto. El main/project_service implementa este puerto.
    """

    @abstractmethod
    def resolve_paths(self, chain_dir: str) -> Any:
        """Resuelve paths del proyecto desde chain_dir."""
        ...

    @abstractmethod
    def get_workflow_json_path(self, chain_dir: str) -> str:
        """Retorna la ruta a workflow.json del proyecto."""
        ...

    @abstractmethod
    def list_available(self) -> list[dict]:
        """Lista proyectos disponibles."""
        ...

    @abstractmethod
    def find_active_sprint(self, project_id: str) -> str:
        """Busca el sprint activo del proyecto."""
        ...


class IConflictResolver(ABC):
    """Puerto para resolución de conflictos del workflow.

    El engine NO conoce delta_tracking. Solo necesita resolver conflictos.
    El main/delta_tracking implementa este puerto.
    """

    @abstractmethod
    def resolve(
        self,
        conflict: Any,
        strategy: str,
        payload: dict[str, Any],
        workflow_state: dict[str, Any],
        specs: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Resuelve un conflicto aplicando la estrategia indicada."""
        ...


class ISprintService(ABC):
    """Puerto para acceso al sprint/spring del workflow.

    El engine NO conoce delta_tracking. Solo necesita consultar/actualizar
    el estado del sprint. El main/delta_tracking implementa este puerto.
    """

    @abstractmethod
    def from_chain_dir(self, chain_dir: str) -> Any:
        """Construye el servicio desde chain_dir."""
        ...

    @abstractmethod
    def load_tasks(self, chain_dir: str) -> list[dict]:
        """Carga las tasks del sprint."""
        ...

    @abstractmethod
    def get_spring(self, spring_id: str) -> dict | None:
        """Obtiene un spring por ID."""
        ...


class IPromptCollector(ABC):
    """Puerto para recolección de prompts/contexto.

    El engine NO conoce features.main.prompts. Solo necesita recolectar
    prompts. El main implementa este puerto.
    """

    @abstractmethod
    def collect(self, chain_dir: str, context: dict[str, Any]) -> dict[str, Any]:
        """Recolecta prompts para el contexto."""
        ...
class IWorkflowLoader(ABC):
    """Puerto para carga de workflow y estado desde disco."""

    @abstractmethod
    def load_workflow(self, workflow_repo: 'WorkflowRepository') -> WorkflowGraph:
        ...

    @abstractmethod
    def load_state(self, chain_dir: str) -> WorkflowState:
        ...

    @abstractmethod
    def save_state(self, state: WorkflowState, chain_dir: str) -> None:
        ...


class ISideEffectOutbox(ABC):
    """Puerto de Outbox de side-effects (ADR-WALKER-067 F4/H).

    Persiste la INTENCION de cada side-effect ANTES de ejecutarlo y su
    resultado despues (completed/failed/compensated). Permite:
      - Idempotencia: reintentar sin duplicar efectos ya completados.
      - Durabilidad: si el proceso cae a mitad del saga, el outbox conserva
        el punto de avance y permite compensar los efectos exitosos previos.
    El engine consume el puerto; la implementacion (en memoria / sqlite /
    remota) la provee el host (composition root).
    """

    @abstractmethod
    def enqueue(self, effect: Any, idempotency_key: str | None) -> Any:
        """Registra la intencion de ejecutar `effect`. Retorna la entrada."""
        ...

    @abstractmethod
    def mark_completed(self, entry: Any, result: Any) -> None:
        """Marca la entrada como completada con su resultado."""
        ...

    @abstractmethod
    def mark_failed(self, entry: Any, error: str) -> None:
        """Marca la entrada como fallida con el error."""
        ...

    @abstractmethod
    def mark_compensated(self, entry: Any, compensation: Any) -> None:
        """Marca la entrada como compensada (efectos previos revertidos)."""
        ...

    @abstractmethod
    def all(self) -> list:
        """Todas las entradas del outbox (para compensacion en cascada)."""
        ...


class IPresenter(ABC):
    """Puerto para presentación de estado del workflow."""

    @abstractmethod
    def show_section(self, title: str) -> None:
        ...

    @abstractmethod
    def show_node_header(
        self, current: str, node: WorkflowNode, sub_state: Any | None = None
    ) -> None:
        ...

    @abstractmethod
    def show_transitions(
        self, workflow: WorkflowGraph, node_id: str, chain_dir: str | None = None
    ) -> None:
        ...

    @abstractmethod
    def show_flow_progress(self, state: WorkflowState) -> None:
        ...


class ITracer(ABC):
    """Puerto para trazabilidad de eventos del workflow (TraceGraph)."""

    @abstractmethod
    def trace(
        self,
        chain_dir: str,
        event_type: str,
        data: dict | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        ...

    @abstractmethod
    def trace_event(self, event: Any) -> None:
        """Registra un evento TIPADO (WorkflowEvent, ADR-WALKER-067 F5/I).

        Contrato tipado de trazabilidad; `trace()` queda como transporte de
        compat mientras se migran los emit-sites.
        """
        ...

    @abstractmethod
    def trace_auto(
        self,
        chain_dir: str,
        sub_state: str,
        stdout: str,
        stderr: str,
        exit_code: int,
        task_id: str | None = None,
    ) -> None:
        ...

    @abstractmethod
    def trace_submit(
        self,
        chain_dir: str,
        sub_state: str,
        decision: str,
        evidence: str,
        task_id: str | None = None,
    ) -> None:
        ...

    @abstractmethod
    def trace_transition(
        self,
        chain_dir: str,
        gate: str,
        from_node: str,
        to_node: str,
        task_id: str | None = None,
    ) -> None:
        ...

    @abstractmethod
    def trace_error(
        self,
        chain_dir: str,
        error_type: str,
        message: str,
        task_id: str | None = None,
    ) -> None:
        ...

    @abstractmethod
    def has_kg_query(
        self,
        chain_dir: str,
        task_id: str = "",
        session_id: str = "",
        layers: set[str] | list[str] | None = None,
    ) -> bool:
        ...

    @abstractmethod
    def replay(self, chain_dir: str, max_events: int | None = None) -> list[dict]:
        ...


class IStateStore(ABC):
    """Puerto de persistencia del WorkflowState (SSOT del estado del sprint).

    Invertido desde domain/state.py (antes importaba infrastructure.repositories
    directamente). El adaptador concreto (WorkflowStateRepository) se registra en
    el composition root.
    """

    @abstractmethod
    def load(self, chain_dir: str) -> WorkflowState:
        """Carga el WorkflowState desde disco (vacío si no existe)."""
        ...

    @abstractmethod
    def save(self, chain_dir: str, state: WorkflowState) -> None:
        """Persiste el WorkflowState en disco."""
        ...


class IConfig(ABC):
    """Puerto de configuracion del engine (sessions_dir, etc.).

    Invertido desde domain/session.py (antes importaba infrastructure.config.get
    directamente). El adaptador concreto (ConfigAdapter) se registra en el
    composition root.
    """

    @abstractmethod
    def get(self, key: str, default: Any = None) -> Any:
        """Resuelve una clave de config (dot-notation)."""
        ...


class ISessionStore(ABC):
    """Puerto de persistencia de sesiones.

    Invertido desde domain/session.py (antes la clase SessionStore vivía en el
    dominio y hacía file-IO). El adaptador concreto (SessionStore en
    infrastructure/session_store.py) se registra en el composition root; el
    dominio solo conoce SessionRecord (dato puro) y este puerto.
    """

    @abstractmethod
    def save(self, record: Any) -> None:
        """Persiste una sesión."""
        ...

    @abstractmethod
    def load(self, session_id: str) -> Any | None:
        """Carga una sesión por ID."""
        ...

    @abstractmethod
    def get_active(self) -> Any | None:
        """Retorna la sesión activa (última usada)."""
        ...

    @abstractmethod
    def set_active(self, session_id: str) -> None:
        """Marca una sesión como activa."""
        ...

    @abstractmethod
    def delete(self, session_id: str) -> None:
        """Elimina una sesión."""
        ...

    @abstractmethod
    def list_sessions(self) -> list:
        """Lista todas las sesiones disponibles."""
        ...

    @abstractmethod
    def find_by_chain_dir(self, chain_dir: str) -> Any | None:
        """Retorna la sesión más reciente para un chain_dir (o None)."""
        ...

    @abstractmethod
    def gc_stale(self, max_hours: int = 168, min_sessions: int = 0) -> int:
        """Borra sesiones stale que no son la activa (GC periódico)."""
        ...

    @abstractmethod
    def ensure_session_dir(self, record: Any) -> str:
        """Crea el directorio de sesión del registro si no existe."""
        ...


class IContextValidator(ABC):
    """Puerto de validacion de contexto del grafo (provides/needs).

    Invertido desde domain/entities.py (antes importaba application.context_resolver
    directamente). El adaptador concreto (ContextResolver) se registra en el
    composition root.

    Resolve_port puede retornar None (no registrado): la validacion de invariantes
    degrada a no-op de forma segura (no rompe tests que no levantan el app).
    """

    @abstractmethod
    def validate_graph(self, graph: WorkflowGraph) -> list[str]:
        """Valida el grafo y retorna lista de errores de contexto."""
        ...


class ISideEffectEngine(ABC):
    """Puerto del motor de side-effects para compensacion (BPMN).

    Invertido desde domain/services/compensation_engine.py (antes importaba
    infrastructure.side_effects directamente). El adaptador concreto
    (SideEffectEngine) se registra en el composition root.
    """

    @abstractmethod
    def run_compensation_handler(self, handler_name: str, ctx: Any) -> Any:
        """Ejecuta un compensation handler por nombre y retorna un Result."""
        ...


class IWorkflowStore(ABC):
    """Puerto UNIFICADO de acceso al workflow (grafo + estado).

    UNIFICACIÓN REAL (ADR-WALKER-067 §7.2): reemplaza `workflow_repo` +
    `state_repo` en el EngineBundle. El engine consume SOLO estos 3 métodos;
    el puerto NO expone repos concretos (anti-patrón "envolver sin desacoplar"
    evitado). El host implementa el adapter (`infrastructure/workflow_store.py`)
    una vez; los use cases / sub-orquestadores del engine usan estos métodos.
    """

    @abstractmethod
    def load_graph(self) -> WorkflowGraph:
        """Carga el grafo/config del workflow."""
        ...

    @abstractmethod
    def load_state(self, chain_dir: str) -> WorkflowState:
        """Carga el estado actual del workflow (default si no existe)."""
        ...

    @abstractmethod
    def save_state(self, state: WorkflowState, chain_dir: str) -> None:
        """Persiste el estado del workflow."""
        ...


class IEngineMetrics(ABC):
    """Puerto de observabilidad del motor (health).

    ADR-WALKER-067 F5/J: el engine expone métricas de salud vía puerto
    (p99 de submit/transition, tamaño de state, contadores). El host puede
    reemplazar el default in-memory por uno que alimente su telemetría.
    """

    @abstractmethod
    def record_submit(self, duration_ms: float) -> None:
        """Registra la duración de un submit."""
        ...

    @abstractmethod
    def record_transition(self, duration_ms: float) -> None:
        """Registra la duración de una transition."""
        ...

    @abstractmethod
    def record_state_size(self, fields: int) -> None:
        """Registra el tamaño del estado (número de campos de sub_state_data)."""
        ...

    @abstractmethod
    def snapshot(self) -> dict:
        """Snapshot de health: p99, counts, últimas duraciones."""
        ...


class ICpgAnalysisPort(ABC):
    """Contrato de análisis y operaciones CPG para tasks públicas (DIP).

    Reubicado desde features/main/task_execution/ports/cpg_analysis_port.py
    (ADR-WALKER-065 D1): el contrato vive con el motor, no con el consumidor.
    code_analysis implementa este puerto.
    """

    @abstractmethod
    def business_layer(self, focus: str = "") -> dict[str, Any]:
        """Devuelve la capa de negocio del CPG (pure functions, flows, patterns, metrics)."""
        ...

    @abstractmethod
    def detect_unused_functions(self, file_filter: str = "") -> dict[str, Any]:
        """Detecta funciones/métodos no usados."""
        ...

    @abstractmethod
    def gather_context(self, files: list[str] | None = None) -> dict[str, Any]:
        """Reúne contexto del CPG para archivos específicos o estructura general."""
        ...

    @abstractmethod
    def review_code(self, files: list[str] | None = None, thresholds: dict | None = None) -> dict[str, Any]:
        """Ejecuta code review (quality gates + bug proposals)."""
        ...

    @abstractmethod
    def project_metrics(self, file_filter: str = "") -> dict[str, Any]:
        """Computa métricas del proyecto."""
        ...

    @abstractmethod
    def scan_dfg(self, action: str = "start", project_root: str = "") -> dict[str, Any]:
        """Data flow scan: inicia (start) o consulta estado (status)."""
        ...

    @abstractmethod
    def build_cpg(self, project_root: str, scan_paths: list[str] | None = None) -> dict[str, Any]:
        """Build/rebuild del CPG desde código fuente."""
        ...

    @abstractmethod
    def index_ref(self, url: str, name: str, branch: str = "main", depth: int = 1, data_dir: str = "") -> dict[str, Any]:
        """Indexa un repo externo como CPG de referencia."""
        ...


__all__ = [
    "IWorkflowIO",
    "IWorkflowLoader",
    "IPresenter",
    "ITracer",
    "ICpgAnalysisPort",
    "IProjectContext",
    "IConflictResolver",
    "ISprintService",
    "IPromptCollector",
    "IStateStore",
    "IConfig",
    "ISessionStore",
    "IContextValidator",
    "ISideEffectEngine",
    "IWorkflowStore",
    "IEngineMetrics",
]
