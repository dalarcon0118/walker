"""infrastructure/repository.py — Persistencia del CKG.

SRP: Unica responsabilidad = leer/escribir el grafo CKG del filesystem.
Soporta .json y .json.gz (gzip comprimido).
"""
from __future__ import annotations
import gzip
import json
from pathlib import Path
from typing import Any

from features.cognitive_knowledge.domain.knowledge_graph import KnowledgeGraph as CodeGraph
from features.cognitive_knowledge.domain.errors import RepositoryError


class JsonRepository:
    """Repository que almacena el CKG como JSON (plano o comprimido)."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            raise RepositoryError(f"No existe {self.path}")
        try:
            if str(self.path).endswith(".gz"):
                return json.loads(gzip.decompress(self.path.read_bytes()).decode())
            return json.loads(self.path.read_text())
        except Exception as e:
            raise RepositoryError(f"Error leyendo {self.path}: {e}")

    def write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if str(self.path).endswith(".gz"):
            self.path.write_bytes(gzip.compress(payload.encode()))
        else:
            self.path.write_text(payload)

    def exists(self) -> bool:
        return self.path.exists()

    def read_graph(self) -> CodeGraph:
        """Lee y reconstructe el CodeGraph completo."""
        data = self.read()
        return CodeGraph.from_dict(data)

    def write_graph(self, graph: CodeGraph) -> dict:
        """Serializa CodeGraph y lo persiste."""
        data = graph.to_dict()
        self.write(data)
        return data
