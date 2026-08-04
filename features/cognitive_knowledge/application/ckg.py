"""application/ckg.py — CodeKnowledgeGraph sobre CognitiveGraph via ODM.

CKG se almacena como entidades KnowledgeFact (ODM) en el indice "ckg"
de CognitiveGraph. Cada nodo se persiste via KnowledgeFact(...).save()
lo que automaticamente construye el TokenIndex para busqueda full-text
y permite usar las relaciones del grafo.
"""

from __future__ import annotations
from pathlib import Path
from returns.result import safe
from returns.pipeline import is_successful
from libs.cognitive_graph import CognitiveGraph
from features.cognitive_knowledge.entities import KnowledgeFact


CKG_INDEX_CONFIG = {
    "fields": {
        "type": "type",
        "scope": "scope",
        "label": "statement",
        "task": "metadata.task_id",
        "description": "rationale",
    },
    "types": {
        "decision": {"description": "Decision de arquitectura"},
        "refactor": {"description": "Refactorizacion aplicada"},
        "convention": {"description": "Convencion de estilo"},
        "pattern": {"description": "Patron de diseno"},
        "rule": {"description": "Regla tecnica"},
        "component": {"description": "Componente reutilizable"},
    },
    "search": {
        "type": "fulltext",
        "fields": ["statement", "rationale"],
    },
    "storage": {"path": "data/ckg.json.gz", "format": "json.gz"},
    "description": "Knowledge Graph: decisiones, convenciones, patrones",
}


class CodeKnowledgeGraph:
    """CKG para desarrollo de software. Persiste via KnowledgeFact(entity).save()."""

    def __init__(self, project_dir: str):
        self.project_dir = Path(project_dir).expanduser().resolve()
        self._cg = CognitiveGraph(str(self.project_dir))
        # Registrar indice con schema ODM si no existe
        if "ckg" not in self._cg.list_indexes():
            self._cg.register_index("ckg", CKG_INDEX_CONFIG)
            KnowledgeFact.bind(self._cg)

    def _next_id(self, prefix: str = "node") -> str:
        """Genera ID secuencial basado en el MAX ID existente (no en el count).

        Usar count+1 colisiona tras borrados: si hay 62 docs y uno se llama
        decision-063 (por un borrado previo), el siguiente seria decision-063
        de nuevo y SOBRESCRIBIRIA el existente.

        Usa la primitiva de infra Index.max_key() que lee el indice en
        memoria (sin limites de search ni parseo manual del storage).
        """
        found = self._cg.max_key("ckg", prefix)
        max_num = found[1] if found else 0
        return f"{prefix}-{max_num + 1:03d}"

    def save_decision(
        self,
        label: str,
        description: str = "",
        decision_type: str = "decision",
        metadata: dict | None = None,
    ) -> dict:
        """Persiste una decision como KnowledgeFact."""
        doc_id = self._next_id(decision_type)
        fact = KnowledgeFact(
            _id=doc_id,
            type=decision_type,
            subject=label.split(":")[0] if ":" in label else label[:40],
            statement=label,
            rationale=description,
            metadata={"task_id": metadata.get("task_id", "") if metadata else ""},
        )
        fact._cg_instance = self._cg
        fact.save()
        return {"ok": True, "node_id": doc_id, "label": label}

    def save_nodes(self, nodes_data: list[dict]) -> dict:
        """Persiste multiples nodos en batch con store_many (1 escritura).

        CICLO DE VIDA DEL CONOCIMIENTO: si un nodo declara 'supersedes'
        (lista de IDs o de statements), las decisiones anteriores que
        reemplaza se marcan como status='superseded'. Asi el agente sabe
        cual es la decision VIGENTE y cual fue degradada (con historia).
        """
        created = []
        degraded = []
        new_docs: dict[str, dict] = {}
        degraded_docs: dict[str, dict] = {}

        for item in nodes_data:
            if not isinstance(item, dict) or "label" not in item:
                continue
            label = item["label"]
            if not label or not str(label).strip():
                continue
            doc_id = self._next_id(item.get("type", "node"))

            # ── Resolver supersedes ──
            supersedes = list(item.get("supersedes", []) or [])
            if supersedes:
                existing = self.query({})
                for old in existing:
                    old_stmt = old.get("statement", old.get("label", ""))
                    old_id = old.get("_id", old.get("id", ""))
                    if any(s in old_stmt or old_stmt in s for s in supersedes):
                        if old_id not in supersedes:
                            supersedes.append(old_id)

            # ── Serializar sin persistir ──
            fact_meta = dict(item.get("metadata", {}) or {})
            _FACT_FIELDS = {
                "label", "description", "rationale", "type", "tags",
                "metadata", "supersedes", "subject", "statement",
                "context", "query_hints", "location", "api", "status",
                "author", "source", "confidence",
            }
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
            fact._cg_instance = self._cg
            new_docs[doc_id] = dict(fact._data)
            created.append(doc_id)

            # ── Degradar decisiones que esta reemplaza ──
            if supersedes:
                existing = self.query({})
                for old in existing:
                    old_id = old.get("_id", old.get("id", ""))
                    if old_id in supersedes and old_id != doc_id:
                        old_meta = dict(old.get("metadata", {}) or {})
                        old_meta["superseded_by"] = label
                        old_meta["status"] = "superseded"
                        old_meta["superseded_at"] = (
                            __import__("datetime").datetime.now(
                                __import__("datetime").timezone.utc
                            ).isoformat()
                        )
                        degraded_docs[old_id] = {
                            **old,
                            "status": "superseded",
                            "metadata": old_meta,
                        }
                        degraded.append(old_id)

        # Batch persist: 1 serializacion para todos
        all_docs = {**new_docs, **degraded_docs}
        if all_docs:
            store_result = _store_batch(self._cg, all_docs)
            if not is_successful(store_result):
                return {
                    "ok": False, "nodes_created": 0, "node_ids": [],
                    "degraded": [], "error": str(store_result.failure()),
                }

        return {
            "ok": True, "nodes_created": len(created), "node_ids": created,
            "degraded": degraded,
        }

    def query(self, filters: dict | None = None) -> list[dict]:
        """Consulta conocimiento via CognitiveGraph search.

        CICLO DE VIDA: por defecto devuelve nodos ACTIVE (vigentes).
        Para incluir degradados, pasar {'status': 'any'} o {'include_superseded': True}.
        """
        filters = dict(filters or {})
        include_all = filters.pop("status", "") == "any" or filters.pop("include_superseded", False)
        result = self._cg.search("ckg", filters or {})
        hits = result["hits"]["hits"]
        if include_all:
            return [self._with_id(h) for h in hits]
        # Solo vigentes: status active (default) o sin marca de degradacion
        active = []
        for h in hits:
            src = self._with_id(h)
            meta = src.get("metadata", {}) or {}
            if meta.get("status") in ("superseded", "deprecated"):
                continue
            active.append(src)
        return active

    @staticmethod
    def _with_id(hit: dict) -> dict:
        """Garantiza que el _source devuelto incluya _id (viene del hit, no del doc)."""
        src = dict(hit.get("_source", {}))
        src.setdefault("_id", hit.get("_id", ""))
        return src

    def delete_by_metadata(self, key: str, value: str) -> dict:
        """Elimina nodos por metadata."""
        result = self._cg.search("ckg", {key: value})
        removed = 0
        for h in result["hits"]["hits"]:
            self._cg.delete("ckg", h["_id"])
            removed += 1
        return {"ok": True, "nodes_removed": removed}


@safe
def _store_batch(cg: CognitiveGraph, docs: dict[str, dict]) -> dict:
    """Core puro del batch persist via store_many (puede lanzar)."""
    return cg.store_many("ckg", docs)
