"""application/services/ckg_service.py — CkgApplicationService.

Application Service (fachada pública) del CKG (ADR-WALKER-048 §7).

Orquesta los Use Cases del feature (`application/use_cases/`), que son
implementación interna del módulo (INV-013). Consumidores externos (tasks,
side-effects, cross-feature orchestrators, CLI/MCP) consumen ESTE servicio,
nunca los Use Cases directos. No contiene lógica de negocio: solo delega
en los use cases y traduce formatos.

También es el destino de la lógica de consulta extraída de
features/main/task_execution/tasks/kg.py (ADR-WALKER-048 §7): la búsqueda
multi-memoria, filtrado y construcción de contexto viven aquí, y knowledge_helpers.py
queda como thin adapter.
"""
from __future__ import annotations

from typing import Any

# ── Questionnaire de extract_knowledge (ADR-051 G4) ──────────────────────
# Guía al LLM para analizar la estructura CPG y poblar el CKG. Vive en el
# Application Service (no en la task) porque es parte del contrato de
# extracción de conocimiento del feature.

_KG_EXTRACT_QUESTIONNAIRE: dict = {
    "instructions": (
        "You received the full code structure via CPG. "
        "Analyze it and answer the following questions. "
        "Your answers will be saved in the CKG (Knowledge Graph) "
        "so they are available in future sprints. "
        "Use 'walker <project>/documenting_knowledge' to save each answer. "
        "IMPORTANT: Do NOT save knowledge that already exists in CKG."
    ),
    "questions": [
        {
            "id": "architecture_patterns",
            "question": "What architecture patterns do you identify?",
            "examples": [
                "Clean Architecture / DDD / Hexagonal",
                "MVC / MVP / MVVM",
                "Microservices / Modular monolith",
                "Event-driven / CQRS",
            ],
            "ckg_type": "decision",
            "scope": "architecture",
        },
        {
            "id": "coding_conventions",
            "question": "What coding conventions do you observe?",
            "examples": [
                "Naming conventions (snake_case, camelCase, PascalCase)",
                "File structure (one file per class, per function)",
                "Use of type hints / docstrings",
                "Import style (absolute, relative)",
            ],
            "ckg_type": "convention",
            "scope": "coding-style",
        },
        {
            "id": "design_decisions",
            "question": "What design decisions are evident from the code?",
            "examples": [
                "Why X technology was used over Y",
                "How dependency injection is handled",
                "Testing strategy",
                "Error / result handling",
            ],
            "ckg_type": "decision",
            "scope": "design",
        },
        {
            "id": "key_components",
            "question": "What are the key components and their relationships?",
            "examples": [
                "Main modules and their responsibilities",
                "Data flow between components",
                "Module dependencies",
                "Extension / plugin points",
            ],
            "ckg_type": "component",
            "scope": "architecture",
        },
        {
            "id": "business_rules",
            "question": "What implicit business rules do you detect?",
            "examples": [
                "Security validations",
                "Authentication / authorization rules",
                "Business logic in services vs entities",
                "State and transition management",
            ],
            "ckg_type": "rule",
            "scope": "business",
        },
        {
            "id": "data_flow",
            "question": "How does data flow through the system?",
            "examples": [
                "Layers: API -> UseCase -> Repository -> DB",
                "Events: Producer -> Broker -> Consumer",
                "Data transformations",
                "Caching strategy",
            ],
            "ckg_type": "pattern",
            "scope": "data-flow",
        },
    ],
}


class CkgApplicationService:
    """Fachada pública de las operaciones del CKG.

    Cada método delega en el Use Case correspondiente, manteniendo las
    firmas que ya consumían las tasks de main (migración sin fricción).
    """

    # ── Escritura ─────────────────────────────────────────────────────

    @staticmethod
    def save_nodes(
        chain_dir: str, nodes: list[dict], task_id: str = "", session_id: str = ""
    ):
        from ..use_cases.save_to_ckg import SaveToCkgUseCase

        return SaveToCkgUseCase.execute(
            chain_dir, nodes, task_id=task_id, session_id=session_id
        )

    @staticmethod
    def collect_knowledge(chain_dir: str, data: dict, task_id: str = ""):
        from ..use_cases.collect_knowledge import CollectKnowledgeUseCase

        return CollectKnowledgeUseCase.execute(chain_dir, data, task_id=task_id)

    @staticmethod
    def index_repo(chain_dir: str, url: str, name: str, branch: str = "main"):
        from ..use_cases.index_repo import IndexRepoUseCase

        return IndexRepoUseCase.execute(chain_dir, url, name, branch)

    @staticmethod
    def research_topic(chain_dir: str, topic: str, query: str = ""):
        from ..use_cases.research_topic import ResearchTopicUseCase

        return ResearchTopicUseCase.execute(chain_dir, topic, query)

    # ── Lectura ───────────────────────────────────────────────────────

    @staticmethod
    def check_health(projects_data_dir: str):
        from ..use_cases.check_ckg_health import CheckCkgHealthUseCase

        return CheckCkgHealthUseCase.execute(projects_data_dir)

    @staticmethod
    def gather_context(
        chain_dir: str,
        query: str = "type:convention OR type:decision",
        task_id: str = "",
    ):
        from ..use_cases.gather_ckg_context import GatherCkgContextUseCase

        return GatherCkgContextUseCase.execute(chain_dir, query=query, task_id=task_id)

    @staticmethod
    def get_nodes_for_task(chain_dir: str, task_id: str, task_files: list[str]):
        from ..use_cases.get_ckg_nodes_for_task import GetCkgNodesForTaskUseCase

        return GetCkgNodesForTaskUseCase.execute(chain_dir, task_id, task_files)

    # ── Traducción de formatos (ADR-048 §7: responsabilidad del Application
    #    Service — payload REST → nodo CKG canónico). Extraído de kg_helpers.py.

    @staticmethod
    def build_ckg_node(payload: dict) -> dict:
        """Transforma un payload REST de save_to_ckg en un nodo CKG canónico."""
        statement = payload.get("statement", payload.get("label", ""))
        rationale = payload.get("rationale", "")
        node_type = payload.get("type", "decision")
        supersedes = payload.get("supersedes", []) or []
        metadata = payload.get("metadata", {}) or {}

        node_meta = dict(metadata)
        if "scope" in payload and "scope" not in node_meta:
            node_meta["scope"] = payload.get("scope")
        if "severity" in payload and "severity" not in node_meta:
            node_meta["severity"] = payload.get("severity")

        node = {
            "label": statement,
            "description": rationale,
            "type": node_type,
            "tags": payload.get("tags", []) or [],
            "metadata": node_meta,
        }
        if supersedes:
            node["supersedes"] = supersedes
        return node

    @staticmethod
    def save_knowledge(
        chain_dir: str,
        project_root: str,
        payload: dict,
        task_id: str = "",
        session_id: str = "",
    ) -> dict:
        """Guarda conocimiento CKG desde un payload REST y formatea la respuesta.

        Traducción de formatos (ADR-048 §7): construye el nodo canónico,
        lo persiste vía save_nodes y devuelve el dict de respuesta con
        note/next_step. Extraído de kg_helpers.save_to_ckg (ADR-051 G3).
        """
        from returns.pipeline import is_successful

        if CkgApplicationService.is_empty_payload(payload):
            return {"ok": False, "saved": False, "message": "no data to save"}

        node = CkgApplicationService.build_ckg_node(payload)
        supersedes = node.get("supersedes", [])
        target = chain_dir or project_root

        result = CkgApplicationService.save_nodes(
            target,
            [node],
            task_id=task_id,
            session_id=session_id,
        )
        if not is_successful(result):
            return {"ok": False, "saved": False, "error": str(result.failure())}
        res = result.value_or({})
        return {
            "ok": bool(res.get("ok", False)),
            "nodes_saved": res.get("nodes_saved", 0),
            "degraded": (res.get("result", {}) or {}).get("degraded", []),
            "note": (
                "La nueva decision reemplaza a las previas; estas quedaron "
                "con status=superseded y solo se ven con include_superseded."
                if supersedes
                else "Decision activa registrada."
            ),
            "next_step": "Run 'walker <project>/recall_knowledge' to verify saved knowledge",
        }

    @staticmethod
    def is_empty_payload(payload: dict) -> bool:
        """True si el payload está vacío o no tiene campos relevantes."""
        if not payload:
            return True
        return not any(
            k in payload
            for k in ("statement", "label", "nodes", "type", "rationale", "metadata")
        )

    @staticmethod
    def schema(searcher: Any, index: str = "") -> dict:
        if index:
            try:
                return searcher.schema(index)
            except KeyError:
                return {
                    "type": "error",
                    "message": f"Index '{index}' not found. Disponibles: {searcher.list_indexes()}",
                }
        return {
            "type": "schema",
            "indexes": searcher.list_indexes(),
        }

    @staticmethod
    def search(searcher: Any, payload: dict, project_id: str = "") -> dict:
        if set(payload.keys()) == {"_index"} or (len(payload) == 1 and "_index" in payload):
            index = payload.get("_index", "ckg")
            try:
                return searcher.search(index, {})
            except KeyError:
                return {
                    "type": "error",
                    "message": f"Index '{index}' not found. Disponibles: {searcher.list_indexes()}",
                }

        has_single_query_keys = any(
            k in payload
            for k in (
                "text",
                "type",
                "file",
                "function",
                "scope",
                "status",
                "severity",
                "tag",
                "label",
                "task",
                "description",
                "signature",
                "kind",
                "domain",
                "include_superseded",
                # Campos del índice trace (ADR-WALKER-055): sin estos,
                # cualquier query estructurada del trace (tool/session/
                # node/sub_state/source/agent) caía en search_many (federado)
                # → error "Indice 'tool' no registrado".
                "tool",
                "session",
                "agent",
                "sprint",
                "node",
                "sub_state",
                "source",
            )
        )
        if not has_single_query_keys and len(payload) > 0:
            return searcher.search_many(CkgApplicationService._with_compact_defaults(payload))

        index = payload.get("_index", "ckg")
        query = {k: v for k, v in payload.items() if k != "_index"}
        include_superseded = query.pop("include_superseded", False)
        if index == "ckg" and include_superseded:
            superseded = CkgApplicationService._search_superseded(project_id, query)
            if superseded:
                return superseded
        try:
            return searcher.search(index, query)
        except KeyError:
            return {
                "type": "error",
                "message": f"Index '{index}' not found. Disponibles: {searcher.list_indexes()}",
            }

    @staticmethod
    def search_many(searcher: Any, payload: dict) -> dict:
        return searcher.search_many(CkgApplicationService._with_compact_defaults(payload))

    @staticmethod
    def _with_compact_defaults(queries: dict) -> dict:
        out = dict(queries or {})
        if isinstance(out, dict):
            out.setdefault("_top_k", 10)
            out.setdefault("_max_chars", 400)
            out.setdefault("_compact", True)
        return out

    @staticmethod
    def _search_superseded(project_id: str, query: dict) -> dict | None:
        try:
            from features.project import ProjectService
            from features.cognitive_knowledge import ckg_service

            paths = ProjectService.resolve_paths(project_id)
            project_root = str(paths.project_root) if paths.project_root else ""
            if not project_root:
                return None
            ckg = ckg_service(project_root)
            nodes = ckg.query({**query, "include_superseded": True})
            return {
                "type": "search",
                "index": "ckg",
                "hits": len(nodes),
                "results": nodes,
            }
        except Exception as e:
            return None

    # ── Extracción de conocimiento (ADR-051 G4) ───────────────────────

    @staticmethod
    def capture_decisions(
        decisions: list,
        project_root: str,
        spec_id: str,
        session_id: str,
        sprint_id: str,
    ) -> list[dict]:
        """Registra decisiones capturadas en el CKG (best-effort con log).

        Extraído de capture_task._capture_decisions (ADR-051 G4): la
        persistencia de decisiones en el CKG es responsabilidad del feature
        cognitive_knowledge, no del task layer.
        """
        import logging

        from returns.pipeline import is_successful

        logger = logging.getLogger(__name__)
        captured_decisions: list[dict] = []
        for dec in decisions:
            if isinstance(dec, str):
                dec = {"statement": dec}
            statement = dec.get("statement", "") or dec.get("title", "")
            if not statement:
                continue
            node = {
                "type": "decision",
                "label": f"decision:{statement[:40]}",
                "description": dec.get("rationale", "") or dec.get("description", ""),
                "metadata": {
                    "task_id": spec_id,
                    "source": f"session:{session_id}",
                    "session_sprint": sprint_id,
                },
            }
            if dec.get("supersedes"):
                node["supersedes"] = dec["supersedes"]
            save_r = CkgApplicationService.save_nodes(project_root, [node])
            if not is_successful(save_r):
                error = save_r.failure()
                logger.warning(
                    "capture_decisions: no se pudo guardar decision '%s' (%s: %s)",
                    statement[:40], type(error).__name__, error,
                )
                continue
            saved = save_r.value_or({})
            captured_decisions.append(
                {
                    "node_id": (saved.get("node_ids") or [None])[0],
                    "statement": statement[:60],
                    "degraded": saved.get("degraded", []),
                }
            )
        return captured_decisions

    @staticmethod
    def build_extract_payload(cpg: Any, scope: str, chain_dir: str) -> dict:
        """Análisis de código para poblar el CKG (extraído de extract_knowledge.py).

        Cruza CPG (puerto inyectado — CpgService.grep) × CKG actual y
        devuelve el payload de extracción: análisis por tipo, facts
        existentes, stats y el questionnaire para el LLM.

        Args:
            cpg: puerto CPG (CpgService) con método grep(query) -> {matches, total}.
            scope: all | functions | classes | files.
            chain_dir: dir del sprint (para consultar el CKG actual).
        """
        analysis = CkgApplicationService._query_cpg_analysis(cpg, scope)
        existing_facts = CkgApplicationService._query_existing_ckg(chain_dir, analysis)
        stats = CkgApplicationService._query_project_stats(cpg, analysis, existing_facts)
        return {
            "stats": stats,
            "existing_ckg_facts_count": len(existing_facts),
            "existing_ckg_facts": existing_facts[:20],
            "cpg_analysis": analysis,
            "questionnaire": _KG_EXTRACT_QUESTIONNAIRE,
        }

    @staticmethod
    def _query_cpg_analysis(cpg: Any, scope: str) -> dict:
        """Consulta el CPG por tipo via grep del puerto (fallback con log)."""
        import logging

        logger = logging.getLogger(__name__)
        from returns.pipeline import is_successful
        from returns.result import safe

        @safe
        def _core() -> dict:
            analysis = {"functions": [], "classes": [], "files": [], "modules": []}
            if scope in ("all", "functions"):
                for m in (cpg.grep("type:function") or {}).get("matches", []):
                    analysis["functions"].append({"name": m.get("name", ""), "file": m.get("file", ""), "line": m.get("line", 0)})
                for m in (cpg.grep("type:method") or {}).get("matches", []):
                    analysis["functions"].append({"name": m.get("name", ""), "file": m.get("file", ""), "line": m.get("line", 0), "method": True})
            if scope in ("all", "classes"):
                for m in (cpg.grep("type:class") or {}).get("matches", []):
                    analysis["classes"].append({"name": m.get("name", ""), "file": m.get("file", ""), "line": m.get("line", 0)})
            if scope in ("all", "files"):
                for m in (cpg.grep("type:file") or {}).get("matches", []):
                    analysis["files"].append({"path": m.get("file", m.get("name", ""))})
                for m in (cpg.grep("type:module") or {}).get("matches", []):
                    analysis["modules"].append({"name": m.get("name", ""), "file": m.get("file", "")})
            return analysis

        result = _core()
        if is_successful(result):
            return result.value_or({"functions": [], "classes": [], "files": [], "modules": []})
        error = result.failure()
        logger.warning("extract_knowledge: análisis CPG falló (%s: %s)", type(error).__name__, error)
        return {"functions": [], "classes": [], "files": [], "modules": []}

    @staticmethod
    def _query_existing_ckg(chain_dir: str, analysis: dict) -> list[dict]:
        """Consulta el CKG actual para evitar duplicados (fallback con log)."""
        import logging

        logger = logging.getLogger(__name__)
        from returns.pipeline import is_successful
        from returns.result import safe

        @safe
        def _core() -> list[dict]:
            facts = []
            for s in CkgApplicationService.query_all(chain_dir):
                facts.append({"type": s.get("type", ""), "statement": s.get("statement", ""), "scope": s.get("scope", "")})
            return facts

        result = _core()
        if is_successful(result):
            return result.value_or([])
        error = result.failure()
        analysis["_error_ckg"] = str(error)
        logger.warning("extract_knowledge: CKG query falló (%s: %s)", type(error).__name__, error)
        return []

    @staticmethod
    def _query_project_stats(cpg: Any, analysis: dict, existing_facts: list) -> dict:
        """Totales del proyecto via grep del puerto (fallback al conteo local)."""
        import logging

        logger = logging.getLogger(__name__)
        from returns.pipeline import is_successful
        from returns.result import safe

        @safe
        def _total(ntype: str) -> int:
            return int((cpg.grep(f"type:{ntype}") or {}).get("total", 0))

        counts = {}
        for ntype in ("function", "class", "file", "module"):
            fallback = len(analysis.get(f"{ntype}s", []))
            r = _total(ntype)
            counts[ntype] = r.value_or(fallback) if is_successful(r) else fallback
        return {
            "total_functions": counts["function"],
            "total_classes": counts["class"],
            "total_files": counts["file"],
            "total_modules": counts["module"],
            "existing_ckg_facts": len(existing_facts),
        }

    # ── Inicialización ────────────────────────────────────────────────

    @staticmethod
    def init_ckg(project_root: str) -> dict:
        """Inicializa el CKG en project_root (vía el puerto — CA-9 ADR-063).

        Construye el grafo con su repo (mapeo de índice) y garantiza el
        directorio de datos. Ya NO escribe el blob `.json.gz` a mano: el
        "CKG vacío" es el estado del índice (sin docs) y los lectores lo
        consumen por el puerto (backend por config), igual con file que con
        sqlite.
        """
        import os

        from features.cognitive_knowledge import ckg_service

        ckg_service(project_root)
        ckg_path = os.path.join(project_root, "data", "ckg.json.gz")
        os.makedirs(os.path.dirname(ckg_path), exist_ok=True)
        return {"initialized": True, "ckg_path": ckg_path}

    # ── Analysis adapter ──────────────────────────────────────────────

    @staticmethod
    def get_analysis_adapter(chain_dir: str) -> Any:
        """Construye el adapter de análisis CKG (ICkgServicePort)."""
        from features.cognitive_knowledge import ckg_analysis_adapter

        return ckg_analysis_adapter(chain_dir)

    # ── Queries adicionales ───────────────────────────────────────────

    @staticmethod
    def query_active(chain_dir: str, type_name: str = "") -> list[dict]:
        """Busca nodos activos (no superseded) en el CKG."""
        from features.project import ProjectService
        from features.cognitive_knowledge import ckg_service

        paths = ProjectService.resolve_paths(chain_dir)
        project_root = str(paths.project_root) if paths.project_root else ""
        if not project_root:
            return []
        ckg = ckg_service(project_root)
        query: dict = {"include_superseded": False}
        if type_name:
            query["type"] = type_name
        return ckg.query(query) or []

    @staticmethod
    def query_by_files(chain_dir: str, files: list[str]) -> list[dict]:
        """Busca nodos en el CKG por archivos."""
        from features.project import ProjectService
        from features.cognitive_knowledge import ckg_service

        paths = ProjectService.resolve_paths(chain_dir)
        project_root = str(paths.project_root) if paths.project_root else ""
        if not project_root:
            return []
        ckg = ckg_service(project_root)
        results = []
        for f in files:
            nodes = ckg.query({"file": f}) or []
            results.extend(nodes)
        return results

    @staticmethod
    def query_all(chain_dir: str) -> list[dict]:
        """Busca todos los nodos en el CKG (sin filtros)."""
        from features.project import ProjectService
        from features.cognitive_knowledge import ckg_service

        paths = ProjectService.resolve_paths(chain_dir)
        project_root = str(paths.project_root) if paths.project_root else ""
        if not project_root:
            return []
        ckg = ckg_service(project_root)
        # ADR-WALKER-063: query_all debe devolver TODOS los nodos. Index.search
        # sin filtros trunca a max_results=50 (metacampo default); con size
        # explícito se recorren todos (dedupe de migración, agent_context, etc).
        return ckg.query({"size": 100000}) or []

    @staticmethod
    def delete_nodes(chain_dir: str, nodes: list[dict]) -> dict:
        """Elimina nodos del CKG."""
        from features.project import ProjectService
        from features.cognitive_knowledge import ckg_service

        paths = ProjectService.resolve_paths(chain_dir)
        project_root = str(paths.project_root) if paths.project_root else ""
        if not project_root:
            return {"ok": False, "deleted": 0}
        ckg = ckg_service(project_root)
        deleted = 0
        for node in nodes:
            node_id = node.get("_id") or node.get("id")
            if node_id and ckg.delete_node(node_id):
                deleted += 1
        return {"ok": True, "deleted": deleted}

    @staticmethod
    def delete_by_metadata(chain_dir: str, key: str, value: str) -> dict:
        """Elimina nodos del CKG por metadata key=value."""
        from features.project import ProjectService
        from features.cognitive_knowledge import ckg_service

        paths = ProjectService.resolve_paths(chain_dir)
        project_root = str(paths.project_root) if paths.project_root else ""
        if not project_root:
            return {"ok": False, "nodes_removed": 0}
        ckg = ckg_service(project_root)
        return ckg.delete_by_metadata(key, value)


def ckg_application_service(project_root: str = "") -> CkgApplicationService:
    """Factory del Application Service (patrón ckg_service/spring_service)."""
    return CkgApplicationService()
