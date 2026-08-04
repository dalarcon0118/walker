"""application/spring_graph_service.py — SpringGraph: fuente unica del spring.

MODELO UNIFICADO (Fase 1): el DeltaGraph es la fuente de la verdad del
spring de negocio (scrum). Un solo indice (delta) almacena:

    Nodo spring:   {_id: "sprint-xxx" o "session-YYYYMMDD", type: "spring",
                    goal, status: planning|active|reviewing|closed,
                    active: bool, created_at}
    Nodo spec:     {_id: "<spring_id>:<spec_id>", type: "spec",
                    objective, domain, status: draft|specified|in_progress|completed,
                    result_state, files, spec_index}
    Edge:          <spring_id> --contains--> <spring_id>:<spec_id>
    Edge:          <spring_id>:<spec_id> --impacts_file--> <file>

RESPONDE CONSULTAS DE NEGOCIO:
    get_active_spring()      -> nodo spring con status active
    get_specs(spring_id)     -> specs del spring (via edges contains)
    get_spec(spring_id, idx) -> spec actual (por spec_index)
    update_spec_status(...)  -> muta status de una spec

PRINCIPIO: solo SpringGraphService escribe en el indice delta. plan.json
queda como PROYECCION de compatibilidad que este mismo servicio regenera
(para no romper consumidores legacy). sprint-state.json fue ELIMINADO:
el estado de negocio del spring vive en el nodo spring del delta.
"""

from __future__ import annotations
import json
import os
from datetime import datetime, timezone


SPRING_STATUS_FLOW = ["planning", "active", "reviewing", "closed"]
SPEC_STATUS_FLOW = [
    "draft",
    "specified",
    "in_progress",
    "completed",
    "rejected",
    "backlog",
]


class SpringGraphService:
    """Operaciones de negocio del spring sobre el indice delta de CognitiveGraph."""

    # ── Constantes de tipos/edges ─────────────────────────────────────

    TYPE_SPRING = "spring"
    TYPE_SPEC = "spec"
    EDGE_CONTAINS = "contains"
    EDGE_IMPACTS = "impacts_file"
    EDGE_DEPENDS = "depends_on"

    def __init__(self, project_root: str):
        """project_root: directorio cognitivo del proyecto (.cognitive/projects/<pid>)."""
        self.project_root = str(project_root)
        self._cg = None

    # ── Resolucion desde chain_dir ────────────────────────────────────

    @classmethod
    def from_chain_dir(cls, chain_dir: str) -> "SpringGraphService | None":
        """Resuelve el servicio desde un chain_dir (directorio del sprint).

        Usado por consumidores del workflow (PromptContextBuilder, providers,
        use-cases) que reciben chain_dir. Deriva project_root via
        ProjectService.resolve_paths (SSOT).
        """
        if not chain_dir:
            return None
        try:
            from features.project import ProjectService

            paths = ProjectService.resolve_paths(chain_dir)
            data_dir = getattr(paths, "data_dir", "") or ""
            project_root = os.path.dirname(data_dir) if data_dir else ""
            if project_root and os.path.isdir(project_root):
                return cls(project_root)
        except Exception:
            pass
        return None

    def ensure_spring_imported(self, chain_dir: str, spring_id: str) -> dict | None:
        """Asegura que el spring del chain_dir exista en el delta.

        Si el spring NO esta en el delta (creado con flujo legacy plan.json),
        lo importa via import_spring (lazy). Asi el delta siempre se vuelve
        la fuente cuando se consulta.
        """
        spring = self.get_spring(spring_id)
        if spring:
            return spring
        try:
            result = self.import_spring(chain_dir)
            if result.get("imported"):
                return self.get_spring(spring_id)
        except Exception:
            pass
        return None

    @property
    def cg(self):
        if self._cg is None:
            from libs.cognitive_graph import CognitiveGraph

            self._cg = CognitiveGraph(self.project_root, auto_register=True)
            self._ensure_index()
        return self._cg

    # ── Indice ────────────────────────────────────────────────────────

    def _ensure_index(self) -> None:
        """Registra el indice delta con schema que soporta spring+spec."""
        if "delta" not in self.cg.list_indexes():
            config = {
                "fields": {
                    "type": "type",
                    "objective": "objective",
                    "domain": "domain",
                    "status": "status",
                    "goal": "goal",
                },
                "types": {
                    "spring": {"description": "Spring (sprint o session informal)"},
                    "spec": {"description": "Especificacion de tarea del spring"},
                },
                "search": {
                    "type": "fulltext",
                    "fields": ["objective", "goal", "domain"],
                },
                "storage": {"path": "data/delta.json.gz", "format": "json.gz"},
                "description": "SpringGraph: springs + specs con estado",
            }
            self.cg.register_index("delta", config)

    # ── Spring CRUD ───────────────────────────────────────────────────

    def create_spring(
        self,
        spring_id: str,
        goal: str = "",
        status: str = "planning",
        project_id: str = "",
        session_id: str = "",
        informal: bool = False,
        display_name: str = "",
    ) -> dict:
        """Crea (o actualiza) un nodo spring en el delta.

        Args:
            informal: Marca el spring como informal (session log de captura
                de tareas). Los springs informales NO compiten como "activo"
                frente a los formales del workflow (ver get_active_spring).
            display_name: Nombre legible/sugerente del spring (el spring_id
                es un identificador técnico, derivado del directorio). Si se
                omite, se deriva del goal (primera frase) o del spring_id.
        """
        self._validate_spring_status(status)
        doc = self._get(spring_id)
        now = datetime.now(timezone.utc).isoformat()
        if doc:
            # Update: preservar campos existentes
            data = dict(doc)
            data.update(
                {
                    "goal": goal if goal else data.get("goal", ""),
                    "status": status,
                    "updated_at": now,
                }
            )
            if informal:
                data["informal"] = True
            if display_name:
                data["display_name"] = display_name
            elif not data.get("display_name"):
                data["display_name"] = self._derive_display_name(
                    data.get("goal", ""), spring_id
                )
        else:
            data = {
                "type": self.TYPE_SPRING,
                "goal": goal,
                "status": status,
                "active": status == "active",
                "project_id": project_id,
                "session_id": session_id,
                "informal": informal,
                "display_name": display_name
                or self._derive_display_name(goal, spring_id),
                "current_spec_index": 0,
                "current_task_index": 0,
                "task_id": "",
                "created_at": now,
                "updated_at": now,
            }
        self._store(spring_id, data)
        return data

    @staticmethod
    def _derive_display_name(goal: str, spring_id: str) -> str:
        """Deriva un nombre legible del goal del spring.

        Toma la primera frase del goal (hasta ':', ';' o '.'), recortada a
        60 chars. Fallback al spring_id si no hay goal.
        """
        import re

        text = (goal or "").strip()
        if text:
            for sep in (":", ";", ".", "—", "-"):
                if sep in text:
                    text = text.split(sep, 1)[0].strip()
                    break
            text = re.sub(r"\s+", " ", text).strip(" ,.")
        if not text:
            return spring_id
        return text if len(text) <= 60 else text[:57].rstrip() + "..."

    def set_spring_status(self, spring_id: str, status: str) -> dict:
        """Actualiza el estado de un spring (planning→active→reviewing→closed)."""
        self._validate_spring_status(status)
        doc = self._get(spring_id)
        if not doc:
            raise ValueError(f"Spring '{spring_id}' not found")
        data = dict(doc)
        data["status"] = status
        data["active"] = status == "active"
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._store(spring_id, data)
        return data

    def set_spring_context(
        self,
        spring_id: str,
        current_spec_index: int | None = None,
        current_task_index: int | None = None,
        task_id: str = "",
    ) -> dict:
        """Actualiza el contexto de negocio del spring (spec actual).

        PRINCIPIO: el estado de negocio del spring (en qué spec estamos,
        cuál es el task_id actual) vive en el NODO spring del delta.
        Sustituye al legacy sprint-state.json/context.

        Args:
            spring_id: Identificador del spring.
            current_spec_index: Índice de la spec actual (0-based).
            current_task_index: Alias legacy del índice (mismo valor).
            task_id: Spec id de la tarea actual.
        """
        doc = self._get(spring_id)
        if not doc:
            raise ValueError(f"Spring '{spring_id}' not found")
        data = dict(doc)
        if current_spec_index is not None:
            data["current_spec_index"] = int(current_spec_index)
        if current_task_index is not None:
            data["current_task_index"] = int(current_task_index)
        if task_id:
            data["task_id"] = task_id
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._store(spring_id, data)
        return data

    def get_spring_context(self, spring_id: str) -> dict:
        """Retorna el contexto de negocio del spring (spec actual).

        Lee del nodo spring del delta. Campos: current_spec_index,
        current_task_index, task_id. Compatible con el formato legacy
        sprint-state.json/context.
        """
        doc = self._get(spring_id) or {}
        return {
            "current_spec_index": doc.get("current_spec_index", 0),
            "current_task_index": doc.get("current_task_index", 0),
            "task_id": doc.get("task_id", ""),
        }

    def get_active_spring(self) -> dict | None:
        """Retorna el spring activo, priorizando el formal del workflow.

        Un spring puede tener status 'active' por dos vias distintas:
          - Formales del workflow (planning → active): planificados via
            plan_sprint, con specs especificadas.
          - Informales de sesion (capture_task session-YYYYMMDD): logs de
            trabajo del agente.

        PRINCIPIO: el "sprint activo" del proyecto es el formal. Si hay
        activos informales compitiendo, se ignoran mientras exista un
        formal activo (fallback al informal si NO hay formal).
        """
        results = self._search({"type": self.TYPE_SPRING, "status": "active"})
        if not results:
            return None
        formals = [r for r in results if not r.get("informal")]
        if formals:
            return self._with_display_name(formals[0])
        return self._with_display_name(results[0])

    def _with_display_name(self, spring: dict) -> dict:
        """Backfill de display_name en lecturas (docs legacy sin el campo)."""
        if not spring.get("display_name"):
            spring["display_name"] = self._derive_display_name(
                spring.get("goal", ""), spring.get("_id", "")
            )
        return spring

    def get_spring(self, spring_id: str) -> dict | None:
        spring = self._get(spring_id)
        return self._with_display_name(spring) if spring else None

    def get_spring_id(self, chain_dir: str) -> str:
        """Deriva el spring_id de un chain_dir (directorio del sprint)."""
        return os.path.basename(os.path.normpath(chain_dir))

    def list_springs(self, status: str = "") -> list[dict]:
        """Lista springs. Con status filtra (ej: closed para historico)."""
        filters = {"type": self.TYPE_SPRING}
        if status:
            filters["status"] = status
        return [self._with_display_name(s) for s in self._search(filters)]

    # ── Spec CRUD ─────────────────────────────────────────────────────

    def add_spec(
        self,
        spring_id: str,
        objective: str,
        domain: str = "",
        spec_type: str = "feature",
        spec_index: int | None = None,
        result_state: str = "",
        files: list[str] | None = None,
        status: str = "draft",
        description: str = "",
        **extra,
    ) -> dict:
        """Agrega una spec al spring. Actualiza si ya existe el id.

        extra: campos legacy completos (predicates, risks, depends_on,
        preconditions, acceptance_criteria, quality_gates, decisions) que
        se preservan en el nodo para compatibilidad con consumidores.
        """
        self._validate_spec_status(status)
        if spec_index is None:
            spec_index = len(self.get_specs(spring_id))
        spec_id = f"SPEC-{spec_index + 1:03d}"
        doc_id = self._spec_doc_id(spring_id, spec_id)

        data = {
            "type": self.TYPE_SPEC,
            "spec_id": spec_id,
            "spec_key": doc_id,  # Identificador global unico: {spring_id}:{spec_id}
            "spec_index": spec_index,
            "objective": objective,
            "domain": domain,
            "spec_type": spec_type,
            "status": status,
            "result_state": result_state,
            "files": files or [],
            "description": description,
            "spring_id": spring_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        # Preservar campos legacy completos si se pasan
        for k in (
            "predicates",
            "risks",
            "depends_on",
            "preconditions",
            "acceptance_criteria",
            "quality_gates",
            "decisions",
            "task_description",
            "validation",
        ):
            if k in extra and extra[k] is not None:
                data[k] = extra[k]
        self._store(doc_id, data)
        # Edge: spring --contains--> spec
        self.cg.add_edge(spring_id, doc_id, type=self.EDGE_CONTAINS)
        # Edge: spec --impacts_file--> file
        for fpath in data["files"]:
            self.cg.add_edge(doc_id, fpath, type=self.EDGE_IMPACTS)
        return data

    def update_spec_status(self, spring_id: str, spec_id: str, status: str) -> dict:
        """Muta el estado de una spec (draft→specified→in_progress→completed)."""
        self._validate_spec_status(status)
        doc_id = self._spec_doc_id(spring_id, spec_id)
        doc = self._get(doc_id)
        if not doc:
            raise ValueError(f"Spec '{doc_id}' not found")
        data = dict(doc)
        data["status"] = status
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._store(doc_id, data)
        return data

    def update_spec(self, spring_id: str, spec_id: str, updates: dict) -> dict:
        """Refina una spec con campos actualizados (merge, no overwrite)."""
        doc_id = self._spec_doc_id(spring_id, spec_id)
        doc = self._get(doc_id)
        if not doc:
            raise ValueError(f"Spec '{doc_id}' not found")
        data = dict(doc)
        # Mapear campos legacy al modelo delta
        mapping = {
            "objective": "objective",
            "domain": "domain",
            "type": "spec_type",
            "result_state": "result_state",
            "task_description": "description",
            "status": "status",
            "predicates": "predicates",
            "risks": "risks",
            "depends_on": "depends_on",
            "preconditions": "preconditions",
            "acceptance_criteria": "acceptance_criteria",
            "quality_gates": "quality_gates",
            "decisions": "decisions",
        }
        for legacy_key, delta_key in mapping.items():
            if legacy_key in updates and updates[legacy_key] is not None:
                data[delta_key] = updates[legacy_key]
        if "validation" in updates and isinstance(updates["validation"], dict):
            preds = updates["validation"].get("predicates")
            if preds is not None:
                data["predicates"] = preds
        if "impacted_artifacts" in updates and isinstance(
            updates["impacted_artifacts"], dict
        ):
            files = updates["impacted_artifacts"].get("files")
            if files is not None:
                data["files"] = files
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._store(doc_id, data)
        return data

    def upsert_spec(
        self,
        spring_id: str,
        objective: str,
        domain: str = "",
        spec_type: str = "feature",
        result_state: str = "",
        files: list[str] | None = None,
        status: str = "draft",
        description: str = "",
        **extra,
    ) -> dict:
        """Upsert por objetivo: si existe una spec con el mismo objective,
        la refina (merge); si no, la agrega al final (SPEC-014+).

        AMPLIFICACIÓN DE DOMINIO sobre la primitiva reusable de infra
        (Entity.update_or_create / CognitiveGraph.upsert): agrega la
        regla delta de match por objective + preservación del spec_id local
        (convention-015: SPEC-001..N es el task_id del workflow).

        CORRECCIÓN del bug de re-planificación: PlanSprintUseCase e
        import_sprint antes pasaban spec_index desde 0, sobrescribiendo
        SPEC-001..N. Aquí el spec_index se continúa desde len(get_specs)
        (auto-append) y el dedupe es por objective (sin duplicados).
        """
        if not objective:
            raise ValueError("upsert_spec requiere 'objective'")
        existing = self._find_spec_by_objective(spring_id, objective)
        if existing is not None:
            updates = {
                "domain": domain,
                "type": spec_type,
                "result_state": result_state,
                "status": status,
                "task_description": description,
            }
            if files:
                updates["impacted_artifacts"] = {"files": files}
            return self.update_spec(spring_id, existing, updates)
        return self.add_spec(
            spring_id,
            objective=objective,
            domain=domain,
            spec_type=spec_type,
            spec_index=None,  # auto-append desde len(get_specs)
            result_state=result_state,
            files=files or [],
            status=status,
            description=description,
            **extra,
        )

    def _find_spec_by_objective(self, spring_id: str, objective: str) -> str | None:
        """Busca una spec del spring por objetivo (match normalizado).

        Retorna el spec_id local (SPEC-00X) de la primera spec cuyo
        objective coincida (exacto tras strip), o None.
        """
        target = objective.strip()
        if not target:
            return None
        for spec in self.get_specs(spring_id):
            if spec.get("objective", "").strip() == target:
                return spec.get("spec_id", spec.get("task_id", ""))
        return None

    def get_specs(self, spring_id: str, status: str = "") -> list[dict]:
        """Retorna las specs del spring, ordenadas por spec_index.

        Compatible con docs legacy (type 'delta' con sprint_id) y nuevos
        (type 'spec' con spring_id). Cada spec incluye spec_key global
        único ({spring_id}:{spec_id}) para distinguir specs de springs
        distintos que comparten spec_id local (SPEC-001..N).
        """
        all_specs = self._search({"type": self.TYPE_SPEC})
        # Incluir docs legacy de tareas (type delta) de este spring
        legacy = self._search({"type": "delta"})
        for l in legacy:
            if l.get("sprint_id") == spring_id:
                # Normalizar a modelo spec
                l.setdefault("spec_id", l.get("task_id", l.get("_id", "")))
                all_specs.append(l)
        specs = [
            s
            for s in all_specs
            if s.get("spring_id") == spring_id or s.get("sprint_id") == spring_id
        ]
        # Normalizar spec_key (backfill para docs sin el campo)
        for s in specs:
            s.setdefault(
                "spec_key",
                self._spec_doc_id(
                    spring_id, s.get("spec_id", s.get("task_id", ""))
                ),
            )
        if status:
            specs = [s for s in specs if s.get("status") == status]
        specs.sort(
            key=lambda s: (
                s.get("spec_index") if s.get("spec_index") is not None else 999
            )
        )
        return specs

    def get_spec(self, spring_id: str, spec_id: str) -> dict | None:
        return self._get(self._spec_doc_id(spring_id, spec_id))

    def get_current_spec(self, spring_id: str) -> dict | None:
        """Retorna la spec 'in_progress' o la ultima no completada."""
        specs = self.get_specs(spring_id)
        if not specs:
            return None
        for s in specs:
            if s.get("status") in ("draft", "specified", "in_progress"):
                return s
        return specs[-1]

    # ── Puente legacy → modelo unificado ──────────────────────────────

    def import_spring(self, sprint_dir: str) -> dict:
        """Importa un spring legacy (plan.json + .workflow-state.json) al modelo unificado.

        Lee plan.json (specs) y el estado del motor (.workflow-state.json)
        para crear nodos spring+spec en el delta con su estado. Permite que
        el SpringGraphService opere sobre springs creados con el flujo formal.

        PRINCIPIO: no depende de sprint-state.json (eliminado estructuralmente).
        El estado de negocio del spring se deriva de las specs del plan.

        Args:
            sprint_dir: Directorio del sprint (contiene plan.json).

        Returns:
            dict con spring_id, specs importados.
        """
        plan_path = os.path.join(sprint_dir, "plan.json")
        if not os.path.exists(plan_path):
            return {"imported": False, "spring_id": "", "specs": 0}
        with open(plan_path) as f:
            plan = json.load(f)
        spring_id = plan.get("chain_id") or os.path.basename(sprint_dir)
        goal = plan.get("goal", "")

        # Estado del spring: derivar desde el workflow del motor
        # (.workflow-state.json). Fallback al estado en plan.json.
        status = "planning"
        wf_state_path = os.path.join(sprint_dir, ".workflow-state.json")
        if os.path.exists(wf_state_path):
            try:
                with open(wf_state_path) as f:
                    wf_state = json.load(f)
                sprint = wf_state.get("sprint", {})
                status = sprint.get("status", "planning")
                if not status or status not in SPRING_STATUS_FLOW:
                    status = "planning"
            except Exception:
                pass
        if status not in SPRING_STATUS_FLOW:
            status = "planning"

        self.create_spring(spring_id, goal=goal, status=status)

        specs = plan.get("specifications", plan.get("tasks", []))
        # Batch import: store_many + edges en vez de N x upsert_spec()
        batch_docs = {}
        for i, item in enumerate(specs):
            spec_id = f"SPEC-{i + 1:03d}"
            doc_id = self._spec_doc_id(spring_id, spec_id)
            batch_docs[doc_id] = {
                "type": self.TYPE_SPEC,
                "spring_id": spring_id,
                "spec_id": spec_id,
                "spec_key": doc_id,
                "spec_index": i,
                "objective": item.get("objective", ""),
                "domain": item.get("domain", ""),
                "spec_type": item.get("type", "feature"),
                "result_state": item.get("result_state", ""),
                "files": (
                    item.get("impacted_artifacts", {}).get("files", [])
                    if isinstance(item.get("impacted_artifacts"), dict)
                    else []
                ),
                "status": item.get("status", "draft"),
                "description": item.get("task_description", ""),
            }
        if batch_docs:
            self.cg.store_many("delta", batch_docs)
            # Crear edges spring -> spec
            for doc_id in batch_docs:
                self.cg.add_edge(spring_id, doc_id, type=self.EDGE_CONTAINS)
        return {"imported": True, "spring_id": spring_id, "specs": len(specs)}

    # ── Proyeccion legacy (plan.json) ───────────────────────────────

    def sync_plan_projection(self, spring_id: str) -> None:
        """Regenera plan.json (proyeccion de specs) para consumidores legacy.

        PRINCIPIO: plan.json es PROYECCION derivada del delta. Este metodo
        es el UNICO que debe regenerarla; nunca editarla directamente.

        Las specs se emiten en formato legacy (impacted_artifacts.files)
        para que los consumidores del workflow (prompt builders, etc.)
        sigan funcionando sin cambios.
        """
        from features.spring.infrastructure.repositories import PlanRepository

        spring = self._get(spring_id) or {}
        specs = self._to_legacy_specs(self.get_specs(spring_id))
        plan = {
            "version": "2.0",
            "chain_id": spring_id,
            "goal": spring.get("goal", ""),
            "workflow": "workflow.v3/",
            "specifications": specs,
        }
        sprint_dir = os.path.join(self.project_root, "data", "spring", spring_id)
        os.makedirs(sprint_dir, exist_ok=True)
        PlanRepository().save(sprint_dir, plan)

    @staticmethod
    def _to_legacy_specs(specs: list[dict]) -> list[dict]:
        """Adapta specs del modelo delta al formato legacy de plan.json."""
        out = []
        for s in specs:
            files = s.get("files", []) or []
            legacy = {
                "spec_id": s.get("spec_id", ""),
                "objective": s.get("objective", ""),
                "type": s.get("spec_type", "feature"),
                "domain": s.get("domain", ""),
                "result_state": s.get("result_state", ""),
                "task_description": s.get("description", ""),
                "status": s.get("status", "draft"),
                "impacted_artifacts": {"files": files},
                "validation": {"predicates": s.get("predicates", [])},
                "depends_on": s.get("depends_on", []),
                "preconditions": s.get("preconditions", []),
                "acceptance_criteria": s.get("acceptance_criteria", []),
                "quality_gates": s.get("quality_gates", []),
                "risks": s.get(
                    "risks", {"curvature": 0.3, "pre_churn": 0.3, "commit_trust": 0.8}
                ),
                "decisions": s.get("decisions", []),
            }
            out.append(legacy)
        return out

    def get_specs_legacy(self, spring_id: str) -> list[dict]:
        """Retorna specs del spring en formato legacy (plan.json).

        Los consumidores del workflow (PromptContextBuilder, TaskProvider)
        esperan specs con impacted_artifacts/validation/risks. Este metodo
        entrega ese formato desde el delta (fuente unica).
        """
        return self._to_legacy_specs(self.get_specs(spring_id))

    # ── Helpers de storage ────────────────────────────────────────────

    def _spec_doc_id(self, spring_id: str, spec_id: str) -> str:
        return f"{spring_id}:{spec_id}"

    def _store(self, doc_id: str, data: dict) -> None:
        self.cg.store("delta", doc_id, data)

    def _get(self, doc_id: str) -> dict | None:
        hit = (
            self.cg._indexes.get("delta").get(doc_id)
            if "delta" in self.cg.list_indexes()
            else None
        )
        if not hit:
            return None
        src = hit.get("_source", hit) if isinstance(hit, dict) else hit
        return dict(src)

    def _search(self, filters: dict) -> list[dict]:
        try:
            result = self.cg.search("delta", filters)
        except KeyError:
            return []
        hits = result.get("hits", {}).get("hits", [])
        out = []
        for h in hits:
            src = h.get("_source", h) if isinstance(h, dict) else h
            if isinstance(src, dict):
                item = dict(src)
                # Normalizar _id del hit (los docs creados por _store no lo
                # incluyen en el _source; los importados/legacy sí).
                if "_id" not in item:
                    hid = h.get("_id") if isinstance(h, dict) else None
                    if hid:
                        item["_id"] = hid
                out.append(item)
        return out

    @staticmethod
    def _validate_spring_status(status: str) -> None:
        if status not in SPRING_STATUS_FLOW:
            raise ValueError(
                f"Invalid spring status '{status}'. Valid: {SPRING_STATUS_FLOW}"
            )

    @staticmethod
    def _validate_spec_status(status: str) -> None:
        if status not in SPEC_STATUS_FLOW:
            raise ValueError(
                f"Invalid spec status '{status}'. Valid: {SPEC_STATUS_FLOW}"
            )
