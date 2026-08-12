"""task_execution/application/intelligence_service.py — Búsqueda consolidada con joins.

Capas de las memorias (ADR-WALKER-023): las tools públicas NO tocan CognitiveGraph
directo. Este servicio orquesta los 4 repositorios (CKG, CPG, Delta, Trace) y cruza
sus resultados por archivo / task_id / texto, devolviendo una respuesta de negocio
(tipo inner/left join) en vez de una concatenación de listas planas.

El servicio vive en task_execution/application (ADI-WALKER-023 decisión): es la capa
que orquesta features sin ser negocio de ninguno. NO depende del engine/workflow.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from returns.pipeline import is_successful
from returns.result import Result, Failure, safe

from features.task_execution.application.contracts import (
    CkgSectionVO,
    CpgSectionVO,
    DeltaSectionVO,
    FailureDiagnosisDTO,
    IntelligenceResultVO,
    KnowledgeDTO,
    QaCoverageDTO,
    ScopeVO,
    SpecDTO,
    SprintEvidenceDTO,
    SummaryVO,
    SymbolDTO,
    TaskFileIntelligenceDTO,
    TraceEventDTO,
    TraceSectionVO,
)

logger = logging.getLogger(__name__)


def _has_file(files: Any, path: str) -> bool:
    """True si `path` aparece en una lista de archivos (con match por sufijo)."""
    if not path:
        return False
    for f in files or []:
        fp = str(f)
        if fp == path or fp.endswith(path) or path.endswith(fp):
            return True
    return False


def build_intelligence_service(chain_dir: str, project_id: str = ""):
    """Construye el TaskIntelligenceService con los 4 repos desde chain_dir.

    Factory que resuelve los repos concretos (Cpg/Ckg/Delta/Trace) a partir de
    un chain_dir. Confiere a los repos en Infrastructure a través del grafo por DI.

    Result pattern (convention-001): el core @safe nunca lanza, ante fallo devuelve
    None con log.
    """
    from features.project import ProjectService

    paths = ProjectService.resolve_paths(chain_dir)
    project_root = str(paths.project_root)
    data_dir = str(paths.project_root)  # CognitiveCpgRepository usa project_root como data_dir
    project_id = paths.project_id or project_id

    # Importar los repos concretos SOLO al construir (evita acoplar el import top-level).
    from features.code_analysis import cpg_repository
    from features.cognitive_knowledge import ckg_repository
    from features.delta_tracking import delta_repository
    from features.event_tracing import trace_repository

    return TaskIntelligenceService(
        cpg_repo=cpg_repository(data_dir, project_id),
        ckg_repo=ckg_repository(project_root),
        delta_repo=delta_repository(project_root),
        trace_repo=trace_repository(),
    )


class TaskIntelligenceService:
    """Orquesta CpgRepository + CkgRepository + DeltaRepository + TraceRepository.

    Constructor: recibe los repos ya resueltos (inyección). El caller construye
    los repos concretos desde chain_dir/project_root vía `build_repos`.
    """

    def __init__(
        self,
        cpg_repo: Any,
        ckg_repo: Any,
        delta_repo: Any,
        trace_repo: Any,
    ):
        self._cpg = cpg_repo
        self._ckg = ckg_repo
        self._delta = delta_repo
        self._trace = trace_repo

    # ── Cores puros por capa (no lanzan; excepción -> Failure) ──

    @safe
    def _cpg_core(self, file: str, task_id: str, text: str) -> CpgSectionVO:
        filters: dict[str, Any] = {}
        if not file:
            filters = {"size": 30}
        elif file:
            filters = {"file": file, "size": 50}
        result = self._cpg.search_nodes(filters)
        hits = (result.get("hits") or {}).get("hits", [])
        files: dict[str, int] = {}
        symbols: list[SymbolDTO] = []
        for h in hits:
            src = h.get("_source", {}) or {}
            f = src.get("file", "") or ""
            if f:
                files[f] = files.get(f, 0) + 1
            if src.get("type") in ("function", "class", "method", "interface", "struct"):
                symbols.append(
                    SymbolDTO(
                        type=src.get("type", ""),
                        name=src.get("name", ""),
                        file=f,
                        line=src.get("line"),
                        signature=(src.get("signature") or "")[:100],
                    )
                )
        total = (result.get("hits") or {}).get("total", 0)
        return CpgSectionVO(
            total=total,
            files=sorted(files.keys())[:20],
            symbols=symbols[:20],
        )

    @safe
    def _ckg_core(self, file: str, task_id: str, text: str) -> CkgSectionVO:
        # Replica la semántica del SSOT GetCkgNodesForTaskUseCase (relevancia por
        # archivos) pero sobre el repo inyectado, sin construir infra duplicada.
        # Si no hay file/task, cae en búsqueda full-text.
        filters: dict[str, Any] = {}
        if file:
            filters["file"] = file
        if task_id:
            filters["metadata.task_id"] = task_id
        nodes = self._ckg.query(filters) if filters else self._ckg.search(text, max_results=15)
        items: list[KnowledgeDTO] = []
        for n in nodes or []:
            if isinstance(n, dict):
                items.append(
                    KnowledgeDTO(
                        id=n.get("id", ""),
                        type=n.get("type", n.get("kind", "")),
                        statement=(n.get("statement") or ""),
                        scope=n.get("scope", ""),
                        tags=(n.get("tags") or [])[:5],
                    )
                )
        return CkgSectionVO(total=len(items), items=items[:15])

    @safe
    def _delta_core(self, file: str, task_id: str, text: str) -> DeltaSectionVO:
        # Composición con el use case de negocio del módulo (QueryDeltaUseCase),
        # no acceso crudo al repo (ADR-WALKER-023: la semántica vive en el dueño).
        from features.delta_tracking.application.use_cases.query_delta import (
            QueryDeltaUseCase,
        )
        from features.delta_tracking.application.use_cases.progress import (
            GetTaskContextUseCase,
        )

        delta_uc = QueryDeltaUseCase(self._delta)
        spring = delta_uc.get_active_spring()
        specs = delta_uc.get_specs(spring.spring_id) if spring else []
        items: list[SpecDTO] = []
        for s in specs:
            sfiles = list(getattr(s, "files", None) or [])
            if file and not _has_file(sfiles, file):
                continue
            if task_id and task_id != getattr(s, "spec_id", ""):
                continue
            # GetTaskContextUseCase enriquece la spec con dependencias + spring goal.
            dependencies: list[dict[str, str]] = []
            spring_goal = ""
            description = ""
            if spring and task_id:
                ctx = GetTaskContextUseCase(self._delta).execute(spring.spring_id, task_id)
                if ctx:
                    dependencies = ctx.get("dependencies", [])
                    spring_goal = ctx.get("spring_goal", "")
                    description = ctx.get("description", "")
            items.append(
                SpecDTO(
                    id=getattr(s, "spec_id", "") or "",
                    objective=(getattr(s, "objective", "") or "")[:120],
                    status=getattr(s, "status", ""),
                    files=sfiles[:5],
                    dependencies=dependencies,
                    spring_goal=spring_goal,
                    description=description,
                )
            )
        return DeltaSectionVO(total=len(items), items=items[:15])

    @safe
    def _trace_core(
        self, file: str, task_id: str, text: str, chain_dir: str = ""
    ) -> TraceSectionVO:
        events = self._trace.load_traces(chain_dir)
        tool_counter: Counter = Counter()
        rows: list[TraceEventDTO] = []
        for ev in events or []:
            if file and not _has_file(getattr(ev, "files", None) or [], file):
                continue
            if task_id and task_id not in (getattr(ev, "task_id", "") or ""):
                continue
            tool_counter[getattr(ev, "tool", "") or "?"] += 1
            rows.append(
                TraceEventDTO(
                    event_type=getattr(ev, "event_type", ""),
                    tool=getattr(ev, "tool", ""),
                    task_id=getattr(ev, "task_id", ""),
                    timestamp=getattr(ev, "timestamp", ""),
                    duration_ms=getattr(ev, "duration_ms", None),
                )
            )
        rows.sort(key=lambda r: r.timestamp, reverse=True)
        return TraceSectionVO(
            total=len(rows),
            tools_top=tool_counter.most_common(5),
            events=rows[:20],
        )

    def _run_section(self, core, *args: Any) -> Any:
        """Ejecuta un core @safe y degrada la sección ante Failure (convention-001).

        Devuelve la sección VO construida (ok=True) o la misma clase VO con
        ok=False y error (sin lanzar). El caller decide con `ok`.
        """
        result = core(*args)
        if is_successful(result):
            return result.value_or(None)
        error = result.failure()
        logger.warning(
            "intelligence: sección fallo (%s: %s)", type(error).__name__, error,
        )
        # Degradar a un VO vacío con ok=False. El core fallido se identifica
        # por identidad (método enlazado) para construir el VO correcto.
        return self._failed_section(core)

    def _failed_section(self, core: Any) -> Any:
        """Devuelve un VO de sección degradado (ok=False, error) según el core."""
        from features.task_execution.application.contracts import (
            CkgSectionVO,
            CpgSectionVO,
            DeltaSectionVO,
            TraceSectionVO,
        )

        error = "sección no disponible"
        if core is self._cpg_core:
            return CpgSectionVO(ok=False, error=error)
        if core is self._ckg_core:
            return CkgSectionVO(ok=False, error=error)
        if core is self._delta_core:
            return DeltaSectionVO(ok=False, error=error)
        if core is self._trace_core:
            return TraceSectionVO(ok=False, error=error)
        return CpgSectionVO(ok=False, error=error)

    def get_task_intelligence(
        self,
        *,
        file: str = "",
        task_id: str = "",
        text: str = "",
        chain_dir: str = "",
    ) -> Result[IntelligenceResultVO, Exception]:
        """Núcleo: consulta las 4 capas y cruza por archivo/task_id.

        Args:
            file: ruta del archivo (join por archivo: cp × ckg × delta × trace).
            task_id: tarea del sprint (join delta × trace).
            text: búsqueda full-text de respaldo en CKG.
            chain_dir: directorio del sprint (solo se usa para obtener trace).

        Returns:
            Result[IntelligenceResultVO, Exception] — VO tipado con las 4
            secciones + summary + insights. Cada sección degrada (ok=False) ante
            fallo de su repo sin tirar el resultado (convention-001).
        """

        @safe
        def _build() -> IntelligenceResultVO:
            cpg_section = self._run_section(self._cpg_core, file, task_id, text)
            ckg_section = self._run_section(self._ckg_core, file, task_id, text)
            delta_section = self._run_section(self._delta_core, file, task_id, text)
            trace_section = self._run_section(self._trace_core, file, task_id, text, chain_dir)

            # ── Joins reales entre memorias: respuestas de negocio ──
            insights = []

            # JOIN file: convenciones que aplican a archivos que tienen specs
            if file and ckg_section.ok and delta_section.ok:
                if delta_section.items and ckg_section.items:
                    insights.append(
                        "Hay %d spec(s) y %d conocimiento(s) que tocan este archivo."
                        % (len(delta_section.items), len(ckg_section.items))
                    )

            # JOIN bug/símbolo: un archivo con símbolos y actividad edit reciente
            if file and cpg_section.symbols and trace_section.ok:
                edits = trace_section.events
                insight = {
                    "file": file,
                    "symbols": len(cpg_section.symbols),
                    "recent_events_tool": list(trace_section.tools_top),
                }
                insights.append(
                    "Archivo cruce: %d símbolo(s) definidos; %d evento(s) reciente(s)."
                    % (len(cpg_section.symbols), len(edits))
                )
                insights.append(insight)

            return IntelligenceResultVO(
                scope=ScopeVO(file=file or None, task_id=task_id or None, text=text or None),
                summary=SummaryVO(
                    cpg_total=cpg_section.total,
                    ckg_total=ckg_section.total,
                    delta_total=delta_section.total,
                    trace_total=trace_section.total,
                ),
                cpg=cpg_section,
                ckg=ckg_section,
                delta=delta_section,
                trace=trace_section,
                insights=insights,
            )

        result = _build()
        if is_successful(result):
            return result
        error = result.failure()
        logger.warning(
            "intelligence: get_task_intelligence fallo (%s: %s)",
            type(error).__name__, error,
        )
        return Failure(error)

    # ── Casos de uso por fase del workflow (ADR-WALKER-023 §3) ──
    #
    # Cada método encapsula el JOIN con propósito de una fase y devuelve una
    # síntesis orientada a la pregunta del agente, no un volcado de tablas.

    def task_file_intelligence(
        self,
        *,
        file: str,
        task_id: str = "",
        chain_dir: str = "",
    ) -> Result[TaskFileIntelligenceDTO, Exception]:
        """Fase IMPLEMENT — contexto completo de un archivo antes de editarlo.

        Intención: "voy a tocar src/auth.py, dame todo lo que necesito saber".
        JOIN con propósito: cpg(file) × ckg(file/scope) × delta(specs del file) × trace(file).
        Devuelve un DTO tipado (contrato de la fase, ADR-043 §4.1).

        Result pattern (convention-001): el core @safe nunca lanza; el wrapper
        decide con is_successful y devuelve Success/ Failure.
        """

        @safe
        def _build() -> TaskFileIntelligenceDTO:
            raw_result = self.get_task_intelligence(
                file=file, task_id=task_id, chain_dir=chain_dir
            )
            raw = raw_result.value_or(IntelligenceResultVO())
            cpg = raw.cpg
            ckg = raw.ckg
            delta = raw.delta
            trace = raw.trace

            convenciones = [i.statement for i in ckg.items]
            specs = [i.objective for i in delta.items]

            symbols = [
                SymbolDTO(
                    type=s.type,
                    name=s.name,
                    file=s.file,
                    line=s.line,
                    signature=s.signature,
                    calls=s.calls,
                )
                for s in cpg.symbols
            ]
            activity = [
                TraceEventDTO(
                    event_type=e.event_type,
                    tool=e.tool,
                    task_id=e.task_id,
                    timestamp=e.timestamp,
                    duration_ms=e.duration_ms,
                )
                for e in trace.events[:5]
            ]

            risk = (
                "HIGH"
                if (specs and ckg.items and trace.total > 0)
                else "MED"
                if specs or ckg.items
                else "LOW"
            )

            return TaskFileIntelligenceDTO(
                file=file,
                symbols=symbols,
                convenciones=convenciones,
                specs=specs,
                activity=activity,
                insights=raw.insights,
                risk=risk,
            )

        result = _build()
        if is_successful(result):
            return result
        error = result.failure()
        logger.warning(
            "intelligence: task_file_intelligence fallo (%s: %s)",
            type(error).__name__, error,
        )
        return Failure(error)

    def failure_diagnosis(
        self,
        *,
        task_id: str,
        chain_dir: str = "",
    ) -> Result[FailureDiagnosisDTO, Exception]:
        """Fase REFINE — ¿por qué falló la tarea y qué convenciones se violaron?

        Intención: "TASK-042 falló QA, dame el contexto para diagnosticar".
        JOIN con propósito: trace(task_id, tools recientes) × ckg(convenciones) × delta(spec).
        Devuelve un DTO tipado (diagnóstico).

        Result pattern (convention-001): core @safe interno + is_successful.
        """

        @safe
        def _build() -> FailureDiagnosisDTO:
            raw_result = self.get_task_intelligence(task_id=task_id, chain_dir=chain_dir)
            raw = raw_result.value_or(IntelligenceResultVO())
            trace = raw.trace
            ckg = raw.ckg
            delta = raw.delta

            events = trace.events
            tools_recientes = [e.tool for e in events[:8]]
            convenciones = [i.statement for i in ckg.items]
            spec_obj = [i.objective for i in delta.items]

            diagnosis = (
                "Sin actividad registrada para la tarea; revisa si se ejecutó "
                "en otra sesión o si el trace no indexó la sesión."
                if not events
                else (
                    "Se detectó actividad reciente: "
                    + ", ".join(t for t in tools_recientes if t)
                    + ". Verifica que la implementación respete "
                    + ("las convenciones aplicables." if convenciones else "los criterios de la spec.")
                )
            )

            return FailureDiagnosisDTO(
                task_id=task_id,
                spec=spec_obj,
                tools_used=tools_recientes,
                convenciones_aplicables=convenciones,
                insights=raw.insights,
                diagnosis=diagnosis,
            )

        result = _build()
        if is_successful(result):
            return result
        error = result.failure()
        logger.warning(
            "intelligence: failure_diagnosis fallo (%s: %s)",
            type(error).__name__, error,
        )
        return Failure(error)

    def qa_coverage(
        self,
        *,
        task_id: str,
        chain_dir: str = "",
    ) -> Result[QaCoverageDTO, Exception]:
        """Fase VERIFY — ¿qué verificaciones/predicados existen para la tarea?

        Intención: "¿qué debe cumplir TASK-042 y hay evidencia de que se hizo?".
        JOIN con propósito: delta(spec predicates/status) × trace(herramientas usadas).

        Result pattern (convention-001): core @safe interno + is_successful.
        """

        @safe
        def _build() -> QaCoverageDTO:
            raw_result = self.get_task_intelligence(task_id=task_id, chain_dir=chain_dir)
            raw = raw_result.value_or(IntelligenceResultVO())
            delta = raw.delta
            trace = raw.trace

            specs = [
                SpecDTO(
                    id=i.id,
                    objective=i.objective,
                    status=i.status,
                    files=i.files,
                    dependencies=i.dependencies,
                    spring_goal=i.spring_goal,
                    description=i.description,
                )
                for i in delta.items
            ]
            activity = [
                TraceEventDTO(
                    event_type=e.event_type,
                    tool=e.tool,
                    task_id=e.task_id,
                    timestamp=e.timestamp,
                    duration_ms=e.duration_ms,
                )
                for e in trace.events[:10]
            ]

            return QaCoverageDTO(
                task_id=task_id,
                specs=specs,
                activity=activity,
                tools_top=trace.tools_top,
                insights=raw.insights,
            )

        result = _build()
        if is_successful(result):
            return result
        error = result.failure()
        logger.warning(
            "intelligence: qa_coverage fallo (%s: %s)",
            type(error).__name__, error,
        )
        return Failure(error)

    def sprint_evidence(
        self,
        *,
        goal: str = "",
        scope: str = "",
        chain_dir: str = "",
    ) -> Result[SprintEvidenceDTO, Exception]:
        """Fase SPRINT.PLANNING — evidencia previa para planear el sprint.

        Intención: "¿qué decisiones/convenciones ya existen sobre esta meta/área?".
        JOIN con propósito: ckg(scope/goal) × delta(tareas relacionadas).

        Result pattern (convention-001): core @safe interno + is_successful.
        """

        @safe
        def _build() -> SprintEvidenceDTO:
            raw_result = self.get_task_intelligence(text=goal or scope or "", chain_dir=chain_dir)
            raw = raw_result.value_or(IntelligenceResultVO())
            ckg = raw.ckg
            delta = raw.delta

            conocimiento = [
                KnowledgeDTO(
                    id=i.id,
                    type=i.type,
                    statement=i.statement,
                    scope=i.scope,
                    tags=i.tags,
                )
                for i in ckg.items[:10]
            ]
            tareas = [
                SpecDTO(
                    id=i.id,
                    objective=i.objective,
                    status=i.status,
                    files=i.files,
                )
                for i in delta.items[:10]
            ]

            return SprintEvidenceDTO(
                scope=scope,
                goal=goal,
                conocimiento_existente=conocimiento,
                tareas_relacionadas=tareas,
                insights=raw.insights,
            )

        result = _build()
        if is_successful(result):
            return result
        error = result.failure()
        logger.warning(
            "intelligence: sprint_evidence fallo (%s: %s)",
            type(error).__name__, error,
        )
        return Failure(error)