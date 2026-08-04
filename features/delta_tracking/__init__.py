"""
features/delta_tracking/ — DeltaTracking: indice de tareas multi-sprint.

Domain:     TokenIndex, IndexEntry (busqueda textual)
Application: index_sprint, index_all, search_tasks
Infrastructure: usa CognitiveGraph como almacenamiento (no persistencia propia)

Uso:
    from features.delta_tracking import DeltaGraphService

    svc = DeltaGraphService(project_dir)
    svc.index_sprint(chain_dir)
    results = svc.search("authentication")
"""
from features.delta_tracking.application.index_service import index_sprint, index_all
from features.delta_tracking.application.search_service import search_tasks
from features.delta_tracking.domain.models import TokenIndex, IndexEntry


class DeltaGraphService:
    """Servicio de DeltaTracking: indexacion y busqueda de tareas.

    Almacena datos via CognitiveGraph (nativo, con grafo + fulltext).
    No persiste nada directamente — CognitiveGraph es el storage.
    """

    def __init__(self, project_dir: str):
        self.project_dir = project_dir
        self._cg = None

    @property
    def cg(self):
        if self._cg is None:
            from libs.cognitive_graph import CognitiveGraph
            self._cg = CognitiveGraph(self.project_dir, auto_register=True)
        return self._cg

    def index_sprint(self, chain_dir: str) -> int:
        """Indexa un sprint en CognitiveGraph (indice delta).

        Lee las specs del DeltaGraph (SpringGraphService — fuente unica)
        y las almacena en el indice delta de CognitiveGraph.
        CognitiveGraph se encarga de construir el TokenIndex (full-text)
        y el grafo (relaciones).

        Args:
            chain_dir: Ruta al directorio del sprint

        Returns:
            Numero de tareas indexadas
        """
        return index_sprint(chain_dir, self.cg)

    def index_all(self) -> int:
        """Indexa todos los sprints del proyecto."""
        return index_all(self.project_dir, self.cg)

    def search(self, query: str, max_results: int = 20) -> list[dict]:
        """Busca tareas por texto.

        Args:
            query: Texto a buscar
            max_results: Maximo de resultados

        Returns:
            Lista de dicts con source, source_id, field, snippet, score
        """
        return search_tasks(self.cg, query, max_results)

    def clear(self) -> None:
        """Limpia el indice delta."""
        from libs.cognitive_graph import TokenIndex as _TI
        # Re-crear el indice delta
        idx = self.cg._indexes.get("delta")
        if idx:
            idx._docs = {}
            idx._graph.clear()
            if idx._token_index is not None:
                idx._token_index = _TI()
            idx._save()
