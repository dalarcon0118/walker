"""domain/services/analysis_service.py — Interfaces de servicios de dominio CPG.

Puertos de salida en Clean Architecture. Definen el contrato de los
servicios de negocio del CPG: análisis de business layer, dead code,
security scanning, impact analysis.

La infraestructura los implementa orquestando CognitiveGraph + lógica
de dominio pura (BusinessAnalyzer, DeadCodeReferenceScanner, etc.).
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class IBusinessAnalyzer(ABC):
    """Contrato para análisis de la capa de negocio del CPG."""

    @abstractmethod
    def analyze(self, scope: dict | None = None) -> dict[str, Any]:
        """Analiza el grafo y produce pure_functions, business_flows, architecture_patterns, metrics."""
        ...


class IDeadCodeDetector(ABC):
    """Contrato para detección de código muerto."""

    @abstractmethod
    def detect(self, scope: dict | None = None) -> list[dict[str, Any]]:
        """Detecta funciones/métodos no usados. Retorna hallazgos serializables."""
        ...


class ISecurityScanner(ABC):
    """Contrato para escaneo de seguridad en el CPG."""

    @abstractmethod
    def scan(self, scope: dict | None = None) -> list[dict[str, Any]]:
        """Detecta flujos de datos sensibles, hardcoded secrets, etc."""
        ...


class IImpactAnalyzer(ABC):
    """Contrato para análisis de impacto de cambios."""

    @abstractmethod
    def analyze_impact(self, node_id: str, depth: int = 2) -> dict[str, Any]:
        """Analiza el impacto de cambiar un nodo del CPG (edges CALLS/IMPACTS)."""
        ...
