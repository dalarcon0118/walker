"""task_execution/ports/cpg_analysis_port.py — Puerto de análisis CPG.

DIP: task_execution define el contrato; code_analysis lo implementa.
Ninguna task pública de task_execution importa código de code_analysis
directamente; todas dependen de esta abstracción.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class ICpgAnalysisPort(ABC):
    """Contrato de análisis y operaciones CPG para tasks públicas."""

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
