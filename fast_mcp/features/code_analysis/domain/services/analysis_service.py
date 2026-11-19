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


def find_unused_functions(
    nodes: dict[str, dict],
    edges: list[dict],
    *,
    is_false_positive_dead_code,
    is_referenced,
    main_guard=None,
    is_fixture=None,
) -> list[dict[str, Any]]:
    """Detecta funciones/métodos sin callers (dead code) — scanner ÚNICO (F7).

    Comparte la lógica entre review_service y build_service (antes duplicada
    en _scan_dead_code y _collect_bug_proposals_core). Filtros inyectados:
      - is_false_positive_dead_code(name, type, signature, path) → bool
      - is_referenced(name, path, line) → bool  (DeadCodeReferenceScanner)
      - main_guard(name, path) opcional → True si debe SKIP (CLI entrypoints)

    Devuelve [{id, name, file, line, is_private}] — cada caller adapta el
    shape a su salida.
    """
    # El edge CALLS va del CALLER al CALLEE (`from` -> `to`). El callee está
    # USADO cuando aparece como `to`; usar `from` marcaba usados a los callers
    # y reportaba como muertas a funciones que sí tenían callers (medido
    # 2026-11-18 con `_match_threshold_core`).
    incoming_ids = {e.get("to") for e in edges if e.get("type") == "CALLS"}
    results: list[dict[str, Any]] = []
    for nid, n in nodes.items():
        if not isinstance(n, dict):
            continue
        if n.get("type") not in ("function", "method") or n.get("is_import"):
            continue
        name = str(n.get("name", ""))
        if nid in incoming_ids or name.startswith("__"):
            continue
        ntype = n.get("type", "")
        sig = str(n.get("signature", "") or "")
        path = str(n.get("path", ""))
        line = int(n.get("line", 0) or 0)
        if is_false_positive_dead_code(
            name, ntype, sig, path, kind=str(n.get("kind", "") or "")
        ):
            continue
        if is_fixture is not None and is_fixture(path, line):
            continue
        if is_referenced(name, path, line):
            continue
        if main_guard is not None and main_guard(name, path):
            continue
        results.append({
            "id": nid, "name": name, "file": path, "line": line,
            "is_private": name.startswith("_"),
        })
    return results


class IImpactAnalyzer(ABC):
    """Contrato para análisis de impacto de cambios."""

    @abstractmethod
    def analyze_impact(self, node_id: str, depth: int = 2) -> dict[str, Any]:
        """Analiza el impacto de cambiar un nodo del CPG (edges CALLS/IMPACTS)."""
        ...
