"""shared/knowledge_graph.py — Knowledge Graph para decisiones de agente IA.

Propósito: El modelo fine-tuneado aprendió patrones, pero una vez liberado
solo recuerda lo que aprendió en entrenamiento. Los RAG solucionan parte,
pero el CKG es más simple: un gestor de conocimiento estructurado para que
el agente TOME DECISIONES informadas sin depender de su memoria interna.

El agente consulta el CKG cuando:
  - Está por implementar algo y quiere saber si ya existe
  - Necesita tomar una decisión técnica y quiere ver antecedentes
  - Quiere entender por qué se hizo algo de cierta manera
  - Busca componentes reutilizables que coincidan con su intención
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ═══════════════════════════════════════════════════════════════════
# Nodo de Conocimiento
# ═══════════════════════════════════════════════════════════════════


@dataclass
class KnowledgeNode:
    """Unidad atómica de conocimiento para decisiones de agente IA.

    Cada nodo responde:
      - QUÉ conocimiento existe
      - POR QUÉ se decidió así
      - CUÁNDO se aplica (contexto + query_hints)
      - DÓNDE está implementado
      - QUÉ reemplaza (historia de decisiones)
    """

    # ── Identidad ──
    id: str  # ID único (ej: "comp-path-resolver-001")
    type: str  # component | pattern | decision | convention | rule | warning

    # ── Qué ──
    subject: str  # Nombre del concepto (ej: "path_resolver")
    statement: str  # Descripción corta: qué hace, para qué sirve

    # ── Por qué ──
    rationale: str = ""  # Por qué se creó o decidió así

    # ── Cuándo aplica (crucial para descubrimiento del agente) ──
    context: str = ""  # En qué situación se usa
    query_hints: list[str] = field(default_factory=list)
    # Palabras clave para que el agente descubra este nodo.
    # El agente busca en query_hints cuando está por implementar algo.
    # Ej: ["resolver ruta", "path absoluto", "project_root", "chain_dir"]

    # ── Dónde ──
    location: str = ""  # Ruta al código o recurso (archivo, URL, módulo)
    api: str = (
        ""  # Firma de la función/clase (ej: "resolve_project_root(chain_dir) -> str")
    )

    # ── Relaciones ──
    supersedes: list[str] = field(default_factory=list)
    # IDs de nodos que ESTE reemplaza (conocimiento anterior).

    # ── Extensibilidad ──
    tags: list[str] = field(default_factory=list)
    # Categorías libres. Cada dominio usa las suyas.
    metadata: dict[str, Any] = field(default_factory=dict)
    # Datos extra específicos del dominio.

    # ── Auditoría ──
    author: str = ""  # Quién generó este conocimiento
    source: str = (
        "manual"  # Origen: manual | code-scan | external-repo | agent-inferred
    )
    confidence: float = 1.0  # 0.0 a 1.0. Baja si fue inferido automáticamente
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "subject": self.subject,
            "statement": self.statement,
            "rationale": self.rationale,
            "context": self.context,
            "query_hints": self.query_hints,
            "location": self.location,
            "api": self.api,
            "supersedes": self.supersedes,
            "tags": self.tags,
            "metadata": self.metadata,
            "author": self.author,
            "source": self.source,
            "confidence": self.confidence,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> KnowledgeNode:
        return cls(
            id=data["id"],
            type=data.get("type", "unknown"),
            subject=data.get("subject", ""),
            statement=data.get("statement", ""),
            rationale=data.get("rationale", ""),
            context=data.get("context", ""),
            query_hints=data.get("query_hints", []),
            location=data.get("location", ""),
            api=data.get("api", ""),
            supersedes=data.get("supersedes", []),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
            author=data.get("author", ""),
            source=data.get("source", "manual"),
            confidence=data.get("confidence", 1.0),
            created_at=data.get("created_at", ""),
        )


# ═══════════════════════════════════════════════════════════════════
# Relación entre nodos
# ═══════════════════════════════════════════════════════════════════


@dataclass
class KnowledgeEdge:
    """Relación semántica entre nodos de conocimiento.

    Tipos de relación:
      - SUPERSEDES:  este nodo reemplaza al otro (historia de decisiones)
      - IMPLEMENTS:  este nodo implementa al otro (pattern → component)
      - CONTRADICTS: este nodo contradice al otro (decisiones alternativas)
      - DEPENDS_ON:  este nodo depende del otro
      - RELATED_TO:  relación genérica
    """

    from_id: str
    to_id: str
    type: str = "RELATED_TO"
    rationale: str = ""  # por qué existe esta relación
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════
# Grafo de Conocimiento
# ═══════════════════════════════════════════════════════════════════


class KnowledgeGraph:
    """Grafo de conocimiento consultable por el agente IA.

    El agente consulta antes de actuar:
      1. domain_info() → descubre qué tipos existen y cómo consultar
      2. search("resolver path") → encuentra path_resolver

    Validación: no permite nodos con campos requeridos vacíos.
    """

    REQUIRED_FIELDS = {"id", "type", "subject", "statement"}
    NON_EMPTY_FIELDS = {"id", "type", "subject", "statement"}

    class ValidationError(Exception):
        pass

    def __init__(self):
        self._nodes: dict[str, KnowledgeNode] = {}
        self._edges: list[KnowledgeEdge] = []

    def _validate_node(self, node: KnowledgeNode) -> None:
        """Valida que un nodo tenga los campos requeridos."""
        missing = []
        for field in self.REQUIRED_FIELDS:
            if not hasattr(node, field):
                missing.append(field)
                continue
            value = getattr(node, field)
            if field in self.NON_EMPTY_FIELDS and not value:
                missing.append(field)
        if missing:
            raise self.ValidationError(
                f"Nodo '{node.id}' campos requeridos vacíos: {', '.join(missing)}"
            )

    # ── Domain schema: el agente descubre cómo consultar ──

    def domain_info(self) -> dict:
        """El agente llama esto primero para saber cómo consultar.

        Similar a GET /index/_mapping de Elasticsearch.
        Deriva el esquema de los datos reales en el grafo.
        Sin strings hardcodeados.
        """
        # Inferir tipos del grafo
        types = {}
        for n in self._nodes.values():
            t = n.type
            if t not in types:
                types[t] = {"count": 0, "sample_subjects": []}
            types[t]["count"] += 1
            if len(types[t]["sample_subjects"]) < 3:
                types[t]["sample_subjects"].append(n.subject)

        return {
            "name": "knowledge-graph",
            "description": "Grafo de conocimiento. El schema se deriva de los datos almacenados.",
            "total_nodes": self.node_count,
            "total_edges": len(self._edges),
            "types": types,
            "queries": {
                "search": "Busca por intención. input: texto libre. output: nodos ordenados por relevancia.",
                "decisions_about": "Conocimiento sobre un tema. input: texto. output: nodos.",
                "why": "Razón detrás de un nodo. input: node_id. output: rationale + contexto.",
                "supersedes_chain": "Historia: qué reemplazó a qué. input: node_id. output: cadena.",
                "what_supersedes": "Qué reemplaza a este. input: node_id. output: lista.",
                "by_type": "Filtra por tipo. input: type name. output: nodos de ese tipo.",
                "tagged": "Busca por tag. input: tag. output: nodos.",
            },
        }

    # ── Nodos ──

    def add_node(self, node: KnowledgeNode) -> None:
        self._validate_node(node)
        self._nodes[node.id] = node

    def get_node(self, nid: str) -> KnowledgeNode | None:
        return self._nodes.get(nid)

    def remove_node(self, nid: str) -> None:
        self._nodes.pop(nid, None)
        self._edges = [e for e in self._edges if e.from_id != nid and e.to_id != nid]

    @property
    def nodes(self) -> dict[str, KnowledgeNode]:
        return self._nodes

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    # ── Edges ──

    def add_edge(self, edge: KnowledgeEdge) -> None:
        if edge.from_id in self._nodes and edge.to_id in self._nodes:
            self._edges.append(edge)

    @property
    def edges(self) -> list[KnowledgeEdge]:
        return self._edges

    # ── Consultas del agente ──

    def search(self, query: str) -> list[dict]:
        """Búsqueda por intención: el agente describe lo que quiere hacer
        y el grafo encuentra conocimiento relevante.

        Busca en: query_hints, subject, statement, tags, context.
        """
        q = query.lower()
        results = []
        for n in self._nodes.values():
            score = 0
            # Coincidencia en query_hints (alta prioridad)
            for hint in n.query_hints:
                if q in hint.lower():
                    score += 3
                elif any(word in hint.lower() for word in q.split()):
                    score += 1
            # Coincidencia en subject
            if q in n.subject.lower():
                score += 2
            # Coincidencia en statement
            if q in n.statement.lower():
                score += 2
            # Coincidencia en tags
            for tag in n.tags:
                if q in tag.lower():
                    score += 1
            # Coincidencia en context
            if q in n.context.lower():
                score += 1
            if score > 0:
                results.append((score, n.to_dict()))

        results.sort(key=lambda x: -x[0])
        return [r[1] for r in results[:10]]

    def decisions_about(self, subject: str) -> list[dict]:
        """¿Qué conocimiento hay sobre X?"""
        q = subject.lower()
        return [
            n.to_dict()
            for n in self._nodes.values()
            if q in n.subject.lower() or q in n.statement.lower()
        ]

    def why(self, node_id: str) -> dict | None:
        """¿Por qué existe este conocimiento?"""
        node = self.get_node(node_id)
        if not node:
            return None
        evidence = [e for e in self._edges if e.to_id == node_id]
        return {
            "node": node.to_dict(),
            "rationale": node.rationale,
            "context": node.context,
            "evidence": evidence,
        }

    def supersedes_chain(self, node_id: str) -> list[dict]:
        """¿Qué conocimiento reemplazó a qué? (historia de decisiones)"""
        chain = []
        current = self.get_node(node_id)
        while current:
            chain.append(current.to_dict())
            if current.supersedes:
                next_id = current.supersedes[0]
                current = self.get_node(next_id)
            else:
                current = None
        return chain

    def what_supersedes(self, node_id: str) -> list[dict]:
        """¿Qué nodos nuevos reemplazan a este?"""
        return [n.to_dict() for n in self._nodes.values() if node_id in n.supersedes]

    def tagged(self, tag: str) -> list[dict]:
        """Busca por tags (extensión de dominio)."""
        return [n.to_dict() for n in self._nodes.values() if tag in n.tags]

    def by_type(self, type_name: str) -> list[dict]:
        """Filtra por tipo de conocimiento."""
        return [n.to_dict() for n in self._nodes.values() if n.type == type_name]

    def query(self, filters: dict | None = None) -> list[dict]:
        """Filtra nodos por campos arbitrarios.

        Args:
            filters: dict con campos y valores a filtrar.
            Ej: {"type": "refactor"}, {"subject": "path"}

        Returns:
            Lista de nodos que coinciden con todos los filtros.
        """
        if not filters:
            return [n.to_dict() for n in self._nodes.values()]
        results = []
        for n in self._nodes.values():
            match = True
            for k, v in filters.items():
                if k == "id" and n.id != v:
                    match = False
                elif k == "type" and n.type != v:
                    match = False
                elif k == "subject" and v not in n.subject:
                    match = False
                elif k == "statement" and v not in n.statement:
                    match = False
                elif k in n.metadata:
                    mv = n.metadata[k]
                    if isinstance(mv, list):
                        if v not in mv:
                            match = False
                    elif str(mv) != str(v):
                        match = False
                elif k == "tag" and v not in n.tags:
                    match = False
                elif k == "tags" and not set(v).intersection(n.tags):
                    match = False
            if match:
                results.append(n.to_dict())
        return results

    # ── Persistencia ──

    def to_dict(self) -> dict:
        return {
            "version": "3.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "edges": [e.__dict__ for e in self._edges],
        }

    @classmethod
    def from_dict(cls, data: dict) -> KnowledgeGraph:
        g = cls()
        raw = data.get("nodes", [])
        if isinstance(raw, dict):
            for nid, ndata in raw.items():
                ndata["id"] = nid
                g._nodes[nid] = KnowledgeNode.from_dict(ndata)
        elif isinstance(raw, list):
            for ndata in raw:
                n = KnowledgeNode.from_dict(ndata)
                g._nodes[n.id] = n
        for edata in data.get("edges", []):
            if isinstance(edata, dict):
                e = KnowledgeEdge(
                    from_id=edata.get("from", edata.get("from_id", "")),
                    to_id=edata.get("to", edata.get("to_id", "")),
                    type=edata.get("type", "RELATED_TO"),
                    rationale=edata.get("rationale", ""),
                    weight=edata.get("weight", 1.0),
                    metadata=edata.get("metadata", {}),
                )
                if e.from_id in g._nodes and e.to_id in g._nodes:
                    g._edges.append(e)
        return g

    def save(self, path: str) -> None:
        import gzip
        import json
        import os

        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))
        if path.endswith(".gz"):
            open(path, "wb").write(gzip.compress(payload.encode()))
        else:
            open(path, "w").write(payload)

    @classmethod
    def load(cls, path: str) -> KnowledgeGraph:
        import gzip
        import json
        import os

        if not os.path.exists(path):
            return cls()
        if path.endswith(".gz"):
            raw = gzip.decompress(open(path, "rb").read()).decode()
        else:
            raw = open(path).read()
        return cls.from_dict(json.loads(raw))
