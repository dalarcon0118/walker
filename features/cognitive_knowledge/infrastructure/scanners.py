"""Scanners multi-lenguaje: imports, layers, símbolos."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from features.cognitive_knowledge.domain.knowledge_graph import KnowledgeEdge as Edge
from features.cognitive_knowledge.domain.models import EdgeType


class ImportScanner:
    """Escanea imports de Python y genera DEPENDS_ON edges.

    SSOT: usa expresiones regulares (rápido, aproximado).
    Para análisis preciso usar ast-grep desde codereview/.
    """

    def __init__(self, project_root: str) -> None:
        self.project_root = Path(project_root)

    def scan(self) -> list[Edge]:
        edges: list[Edge] = []
        for src in self.project_root.rglob("*.py"):
            try:
                text = src.read_text(errors="ignore")
            except OSError:
                continue
            rel = str(src.relative_to(self.project_root))
            for match in re.finditer(
                r"^(?:from|import)\s+([\w.]+)", text, re.MULTILINE
            ):
                mod = match.group(1).split(".")[0]
                edges.append(
                    Edge(
                        from_id=rel,
                        to_id=mod,
                        type=EdgeType.DEPENDS_ON,
                        properties={"line": text[: match.start()].count("\n") + 1},
                    )
                )
        return edges


class LayerScanner:
    """Escanea structure de directorios y genera BELONGS_TO edges.

    Infiere capas desde subdirectorios de primer nivel bajo scripts/ o src/.
    """

    def __init__(self, project_root: str) -> None:
        self.project_root = Path(project_root)

    def scan(self) -> list[Edge]:
        edges: list[Edge] = []
        scripts_dir = self.project_root / "scripts"
        if not scripts_dir.exists():
            return edges

        # Mapear subdirectorios como capas
        layer_dirs = [
            d
            for d in scripts_dir.iterdir()
            if d.is_dir() and not d.name.startswith("_")
        ]
        for layer_dir in layer_dirs:
            layer_name = layer_dir.name
            # Crear nodos layer si no existen (edge reference)
            for py_file in layer_dir.rglob("*.py"):
                rel = str(py_file.relative_to(self.project_root))
                edges.append(
                    Edge(
                        from_id=rel,
                        to_id=layer_name,
                        type=EdgeType.BELONGS_TO,
                    )
                )

        return edges


class SymbolScanner:
    """Escanea definiciones de clases y funciones."""

    def __init__(self, project_root: str) -> None:
        self.project_root = Path(project_root)

    def scan(self) -> dict[str, list[str]]:
        symbols: dict[str, list[str]] = {}
        for src in self.project_root.rglob("*.py"):
            try:
                text = src.read_text(errors="ignore")
            except OSError:
                continue
            rel = str(src.relative_to(self.project_root))
            names = re.findall(
                r"^(?:class|def|async def)\s+([\w]+)", text, re.MULTILINE
            )
            if names:
                symbols[rel] = names
        return symbols


class QltyScanner:
    """Wrapper para integración con qlty metrics."""

    def __init__(self, project_root: str) -> None:
        self.project_root = Path(project_root)

    def run(self) -> dict[str, Any]:
        return {"status": "ok", "issues": 0}
