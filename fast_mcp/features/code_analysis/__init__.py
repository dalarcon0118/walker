"""CPG — Code Property Graph builder + query service.

PROPÓSITO (taxonomía de memorias, project.md): memoria del CÓDIGO del
proyecto — leer y buscar código vía consultas estructuradas (fulltext +
filtros SQL) y casos de uso (outline, scan de bugs, queries de grafo).
Estado canónica: 0 docs hasta el rebuild de derivables (H-6, ADR-075).

FACHADA PEREZOSA (PEP 562 — ADR-WALKER-053): la implementación vive en
builder.py y submódulos; este init NO importa nada eager (rompe el ciclo
con cpg_service). API pública idéntica:
    API pública:
    from fast_mcp.features.code_analysis import (
        CPGBuilder, CpgQueryService, CpgService, cpg_service,
        CheckCpgHealthUseCase, run_outline, parse_outline,
        cpg_repository, cpg_analysis_adapter, CodeNode,
    )

Composition root: cpg_service() centraliza la construccion del builder/query
(patron espejo de delta_tracking.spring_service / cognitive_knowledge.ckg_service).
Las factories cpg_repository()/cpg_analysis_adapter() encapsulan la composicion
de infraestructura (repo por port + adapters). Los consumidores importan desde
la RAIZ del paquete, NUNCA desde rutas internas (application/, infrastructure/).
Si la implementacion cambia de lugar o se reemplaza, solo se actualiza este
modulo — los consumidores no se tocan.

Arquitectura DDD + Hexagonal:
    domain/         → entities, values, events, services
    application/    → BuildGraphUseCase, QueryGraphUseCase, ScanBugsUseCase, CpgService
    infrastructure/ → repository, ast_grep_adapter, filters
"""
from __future__ import annotations

from typing import Any

_EXPORTS: dict[str, str] = {
    "CPGBuilder": "features.code_analysis.domain.builder",
    "CpgQueryService": "features.code_analysis.domain.builder",
    "cpg_service": "features.code_analysis.domain.builder",
    "cpg_repository": "features.code_analysis.domain.builder",
    "cpg_analysis_adapter": "features.code_analysis.domain.builder",
    "CpgService": "features.code_analysis.application.services.cpg_service",
    "CodeAnalysisService": "features.code_analysis.application.services.code_analysis_service",
    "CheckCpgHealthUseCase": "features.code_analysis.application.use_cases",
    "run_outline": "features.code_analysis.infrastructure.ast_grep_adapter",
    "parse_outline": "features.code_analysis.infrastructure.ast_grep_adapter",
    "cpg_repository_impl": "features.code_analysis.infrastructure.repository",
    "cpg_analysis_adapter_impl": "features.code_analysis.infrastructure.adapters",
    "CodeNode": "features.code_analysis.domain.entities",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {name!r} not found")
    import importlib

    return getattr(importlib.import_module(target), name)


def __dir__() -> list[str]:
    return sorted(list(globals()) + list(_EXPORTS))
