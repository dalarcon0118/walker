"""infrastructure/cognitive_ckg_repo.py — Implementa CkgRepository via CognitiveGraph.

Capa de infraestructura en Clean Architecture. Traduce operaciones de dominio
a CognitiveGraph (store_many batch, search O(1), delete_many).
"""
from __future__ import annotations

from datetime import datetime, timezone

from returns.result import safe
from returns.pipeline import is_successful


from features.cognitive_knowledge.application.ports.ckg_repository import CkgRepository
from features.cognitive_knowledge.entities import KnowledgeFact
from app.cognitive_registry import memory_data_dir
from shared.libs.cognitive_graph.config import load_memory_connection


def _resolve_graph_factory() -> "Any | None":
    """Fallback DI (ADR-045/058): resuelve graph_factory desde el service locator neutro.

    Igual que CognitiveDeltaRepository: el composition root inyecta su
    resolutor vía set_service_locator(); el repo resuelve 'cognitive_graph'
    sin importar features/main. Fail-open: None si no está disponible.
    """
    from shared.core.engine.bootstrap import resolve_locator

    return resolve_locator("cognitive_graph")

# Campos canónicos de un nodo; el resto se normaliza a metadata.
_FACT_FIELDS = {
    "label", "description", "rationale", "type", "tags",
    "metadata", "supersedes", "subject", "statement",
    "context", "query_hints", "location", "api", "status",
    "author", "source", "confidence",
}


class CognitiveCkgRepository(CkgRepository):
    """Implementacion sobre CognitiveGraph (indice 'ckg').

    El orquestador CognitiveGraph se recibe por constructor injection
    (graph_factory). DEBE proveerse; no hay fallback al service locator.
    """

    def __init__(self, project_root: str, graph_factory=None):
        if graph_factory is None:
            # ADR-045: fallback al composition root (service locator neutro).
            graph_factory = _resolve_graph_factory()
        if graph_factory is None:
            raise ValueError(
                "CognitiveCkgRepository requiere graph_factory inyectado. "
                "Pásalo desde el composition root (app.bootstrap)."
            )
        # ADR-WALKER-061 (F6): la conexión se lee de CONFIG (env).
        self._cg = graph_factory(load_memory_connection(memory_data_dir(str(project_root))))
        if "ckg" not in self._cg.list_indexes():
            self._cg.register_index("ckg", {
                "fields": {
                    "type": "type", "scope": "scope",
                    "label": "statement", "description": "rationale",
                    "tag": "tags", "task": "metadata.task_id",
                },
                "types": {
                    "decision": {}, "refactor": {}, "convention": {},
                    "pattern": {}, "rule": {}, "component": {},
                },
                "search": {"type": "fulltext", "fields": ["statement", "rationale"]},
            })

    def save_nodes(self, nodes_data: list[dict]) -> dict:
        """Batch persist con logica completa de dominio (supersedes, metadata)."""
        degraded = []
        new_docs: dict[str, dict] = {}
        degraded_docs: dict[str, dict] = {}
        # Escaneo único del índice para el procesamiento de supersedes de TODA
        # la llamada (una sola lectura, no una por nodo — O(n²) → O(n)).
        existing = self.query({})

        # Fix colisión de IDs en batch: next_id() consulta max_key contra el
        # índice PERSISTIDO, que NO incluye los nodos del batch actual (el
        # store es al final). Sin offset local, N nodos del mismo tipo
        # colisionan en el mismo doc_id y new_docs[doc_id] los sobrescribe
        # silenciosamente (bug diagnosticado: 6 nodos enviados → 2 persistidos).
        batch_counters: dict[str, int] = {}

        for item in nodes_data:
            if not isinstance(item, dict) or "label" not in item:
                continue
            label = item["label"]
            if not label or not str(label).strip():
                continue
            t = item.get("type", "node")
            batch_counters[t] = batch_counters.get(t, 0) + 1
            doc_id = self.next_id(t, offset=batch_counters[t])

            # ── Resolver supersedes (match por statement contra el índice) ──
            supersedes = list(item.get("supersedes", []) or [])
            if supersedes:
                for old in existing:
                    old_stmt = old.get("statement", old.get("label", ""))
                    old_id = old.get("_id", old.get("id", ""))
                    if any(s in old_stmt or old_stmt in s for s in supersedes):
                        if old_id not in supersedes:
                            supersedes.append(old_id)

            # ── Normalizar campos extra a metadata ──
            fact_meta = dict(item.get("metadata", {}) or {})
            for k, v in item.items():
                if k not in _FACT_FIELDS and v is not None:
                    fact_meta.setdefault(k, v)

            fact = KnowledgeFact(
                _id=doc_id,
                type=item.get("type", "decision"),
                subject=label.split(":")[0] if ":" in label else label[:40],
                statement=label,
                rationale=item.get("description", "") or item.get("rationale", ""),
                metadata=fact_meta,
                tags=item.get("tags", []),
            )
            # Seriación vía API pública (to_dict), no atributos privados.
            new_docs[doc_id] = fact.to_dict()

            # ── Degradar decisiones que esta reemplaza ──
            if supersedes:
                for old in existing:
                    old_id = old.get("_id", old.get("id", ""))
                    if old_id in supersedes and old_id != doc_id:
                        old_meta = dict(old.get("metadata", {}) or {})
                        old_meta["superseded_by"] = label
                        old_meta["status"] = "superseded"
                        old_meta["superseded_at"] = (
                            datetime.now(timezone.utc).isoformat()
                        )
                        degraded_docs[old_id] = {
                            **old,
                            "status": "superseded",
                            "metadata": old_meta,
                        }
                        degraded.append(old_id)

        all_docs = {**new_docs, **degraded_docs}
        if all_docs:
            store_result = _store_batch(self._cg, all_docs)
            if not is_successful(store_result):
                return {
                    "ok": False, "nodes_created": 0, "node_ids": [],
                    "degraded": [], "error": str(store_result.failure()),
                }

        # Veracidad de nodes_created: reportar lo que REALMENTE sobrevivió al
        # dict (claves únicas), no los items iterados (un batch con IDs
        # colisionantes reportaba 6 aunque solo persistían 2).
        return {
            "ok": True,
            "nodes_created": len(new_docs),
            "node_ids": list(new_docs.keys()),
            "degraded": degraded,
        }

    def query(self, filters: dict | None = None) -> list[dict]:
        filters = dict(filters or {})
        include_all = filters.pop("status", "") == "any" or filters.pop("include_superseded", False)
        result = self._cg.search("ckg", filters or {})
        hits = result["hits"]["hits"]
        all_nodes = []
        for h in hits:
            src = dict(h.get("_source", {}))
            src.setdefault("_id", h.get("_id", ""))
            all_nodes.append(src)
        if include_all:
            return all_nodes
        return [
            n for n in all_nodes
            if (n.get("metadata", {}) or {}).get("status") not in ("superseded", "deprecated")
        ]

    def search(self, query: str, max_results: int = 20) -> list[dict]:
        result = self._cg.search("ckg", {"text": query, "size": max_results})
        hits = result["hits"]["hits"]
        return [
            {**h.get("_source", {}), "_id": h.get("_id", "")}
            for h in hits
        ]

    def delete_by_metadata(self, key: str, value: str) -> dict:
        # ADR-WALKER-063: size explícito alto — search({}) sin filtros o con
        # filtro no indexado trunca a 50 hits y el borrado quedaría parcial.
        result = self._cg.search("ckg", {key: value, "size": 100000})
        ids = {h["_id"] for h in result["hits"]["hits"]}
        if ids:
            return self._cg.delete_many("ckg", ids)
        return {"ok": True, "nodes_removed": 0}

    def next_id(self, prefix: str = "node", offset: int = 0) -> str:
        """Devuelve el siguiente ID para un prefijo, sobre el índice PERSISTIDO.

        `offset` permite generar IDs únicos dentro del MISMO batch: como el
        índice persistido no incluye los nodos aún no commiteados, el caller
        pasa un contador local (1..N) y aquí se suma al máximo persistido —
        así N nodos del mismo tipo en un batch NO colisionan (fix de pérdida
        silenciosa de nodos por colisión de IDs en save_nodes).
        """
        found = self._cg.max_key("ckg", prefix)
        max_num = found[1] if found else 0
        return f"{prefix}-{max_num + offset:03d}"


@safe
def _store_batch(cg: CognitiveGraph, docs: dict[str, dict]) -> dict:
    return cg.store_many("ckg", docs)
