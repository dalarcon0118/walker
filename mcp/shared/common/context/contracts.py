"""shared/common/context/contracts.py — Contrato de plugin de contexto (ContextProvider).

El ABC ContextProvider define el contrato que toda feature de negocio
implementa para aportar capas de contexto (cpg, ckg, trace, state, delta...).

Vive en libs (capa neutral, DIP) para que las features NO dependan del engine.
El mecanismo de composición (ContextProviderRegistry, ContextComposer,
QueryManager) vive en features/engine/context y consume este contrato.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import ContextPiece


class ContextProvider(ABC):
    """Fuente de contexto configurable por nodo.

    Cada provider representa una CAPA de datos que el nodo puede declarar.
    No asume inteligencia en el workflow — el provider se PRESENTA al LLM
    como una capa con contenido y propósito.

    Dos salidas:
      - instructions(): texto que va al system prompt. Declara qué capa es,
        qué contiene, cómo usarla, con qué combinarse.
      - collect(): los datos concretos de la capa para el context layer.
    """

    @abstractmethod
    def instructions(self, config: dict, ctx: Any) -> str:
        """Declaración de capa para el LLM.

        No enseña queries — declara qué contiene esta capa, cómo usarla
        eficientemente, y con qué otras capas se combina mejor.

        Retorna string vacío si no hay instrucciones particulares.
        """
        return ""

    @abstractmethod
    def collect(self, config: dict, ctx: Any, query_manager: Any = None) -> ContextPiece | None:
        """Recupera los datos concretos de esta capa.

        Los providers que consultan capas de datos (CPG, CKG, Trace, State)
        reciben un QueryManager para hacer las consultas. Los providers
        que solo dan instrucciones pueden ignorarlo.

        Retorna None si esta capa solo existe para dar instrucciones
        y no tiene datos que entregar.
        """
        ...

    def schema(self) -> dict:
        """JSON Schema de la config que acepta este provider."""
        return {}

    def output_schema(self) -> dict:
        """JSON Schema de la CAPA de datos que produce este provider (F2 SPEC-043).

        SSOT del contexto de entrada: la forma de la capa que el agente recibe.
        Default fail-open {} (provider sin DTO declarado). Los providers pueden
        devolver el schema derivado de engine/context/contracts.py (CONTEXT_SCHEMAS)
        o uno propio.
        """
        return {}
