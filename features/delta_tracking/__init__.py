"""
features/delta_tracking/ — DeltaTracking: indice de tareas multi-sprint.

Domain:     Spring, Spec (entidades puras)
Application: SpringService (thin orchestrator), use cases, index_service
Infrastructure: CognitiveDeltaRepository

MECANISMO DE INVERSION DE DEPENDENCIAS (patron CPG):
  - Port:       DeltaRepository (application/ports) — contrato de persistencia
  - Servicio:   SpringService inyectable (repo: DeltaRepository | None = None)
  - Fachada:    los consumidores importan desde la RAIZ del paquete
                (from features.delta_tracking import SpringService, spring_service)
                NUNCA desde la ruta interna application/services/. Si la
                implementacion cambia de lugar o se reemplaza, solo se
                actualiza este modulo — los consumidores no se tocan.
  - Composition root: spring_service() centraliza la construccion del
                servicio (desde chain_dir o project_root).

La clase legacy DeltaGraphService fue eliminada (SSOT #5): delegaba en
index_sprint/index_all sin agregar valor y clear() manipulaba internals de
CognitiveGraph. La busqueda (search_service.search_tasks) tenia como unico
caller a DeltaGraphService.search — sin consumidores externos — por lo que
search_service.py fue eliminado como codigo muerto. La busqueda sobre el
delta se hace via CognitiveGraph.search directo (kg_search/kg_multi).
"""

from __future__ import annotations

from features.delta_tracking.application.ports.delta_repository import DeltaRepository
from features.delta_tracking.application.services.spring_service import SpringService

__all__ = ["SpringService", "DeltaRepository", "spring_service", "ConflictDetector"]


def spring_service(
    chain_dir: str = "",
    project_root: str = "",
    repo: DeltaRepository | None = None,
    graph_factory=None,
) -> SpringService | None:
    """Composition root del Delta: construye SpringService con su repo.

    Punto unico de construccion: los consumidores no deciden como resolver
    el servicio ni que repositorio usar. Si mañana cambia la firma del
    constructor o el repo por defecto, solo se toca esta funcion.

    Args:
        chain_dir: Ruta al directorio del sprint (resuelve project_root).
        project_root: Directorio cognitivo del proyecto (si no hay chain_dir).
        repo: Repositorio inyectado (tests/alternativas) — por defecto
              CognitiveDeltaRepository.
        graph_factory: Factory para CognitiveGraph (inyeccion DI). Si no se
              provee, CognitiveDeltaRepository lo resuelve internamente.

    Returns:
        SpringService construido, o None si no hay contexto resolvible.
    """
    if chain_dir:
        return SpringService.from_chain_dir(chain_dir, graph_factory=graph_factory)
    if project_root:
        return SpringService(project_root, repo=repo, graph_factory=graph_factory)
    return None
