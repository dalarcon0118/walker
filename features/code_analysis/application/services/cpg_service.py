"""application/services/cpg_service.py — CpgService (thin orchestrator CPG).

Capa de aplicacion en Clean Architecture. Orquesta operaciones del CPG
delegando en BuildGraphUseCase y CpgRepository.

Wrapper delgado sobre BuildGraphUseCase que expone la misma API pero
inyecta el repositorio via puerto (CpgRepository).
"""
from __future__ import annotations

from features.code_analysis.application.ports.cpg_repository import CpgRepository
from features.code_analysis.infrastructure.cognitive_cpg_repo import CognitiveCpgRepository
from features.code_analysis.application.build_service import BuildGraphUseCase


class CpgService:
    """Orquestador delgado del CPG (build, update, search)."""

    def __init__(
        self,
        project_id: str,
        data_dir: str,
        code_root: str,
        code_paths: list[str] | None = None,
        repo_path: str = "",
        repo: CpgRepository | None = None,
        chain_dir: str = "",
    ):
        self._repo = repo or CognitiveCpgRepository(data_dir, project_id)
        self._inner = BuildGraphUseCase(
            project_id=project_id,
            data_dir=data_dir,
            code_root=code_root,
            code_paths=code_paths,
            repo_path=repo_path,
        )
        self._chain_dir = chain_dir

    # ── Build ──

    def build(self, scan_paths: list[str] | None = None) -> dict:
        """Full rebuild del CPG."""
        return self._inner.build(scan_paths)

    def update(self, refresh: bool = False) -> dict:
        """Incremental via watcher (solo archivos cambiados)."""
        return self._inner.update(refresh)

    def index_task_files(self, task_files: list[str]) -> dict:
        """Re-indexa solo archivos especificos."""
        return self._inner.index_task_files(task_files)

    # ── Search ──

    def search(self, filters: dict, max_results: int = 50, offset: int = 0) -> dict:
        """Busca nodos en el CPG."""
        return self._repo.search_nodes(filters, max_results, offset)

    def get_node(self, node_id: str) -> dict | None:
        return self._repo.get_node(node_id)

    # ── Stats ──

    def stats(self) -> dict:
        return self._repo.get_stats(self._inner.project_id)

    def scan_dfg(self, scan_paths: list[str] | None = None) -> dict:
        return self._inner.scan_dfg(scan_paths)

    # ── Query helpers (CpgQueryService facade) ──

    def grep(self, query: str) -> dict:
        """Busca nodos por query DSL (wrapper de CpgQueryService.grep)."""
        from features.code_analysis.application.query_service import QueryGraphUseCase
        from features.project import ProjectService

        paths = ProjectService.resolve_paths(self._chain_dir or "")
        data_dir = str(paths.data_dir) if paths.data_dir else ""
        q = QueryGraphUseCase(
            chain_dir=self._chain_dir or "",
            project_id=self._inner.project_id,
            data_dir=data_dir or None,
        )
        return q.grep(query)

    def read(self, file_path: str, verbose: bool = False) -> dict:
        """Lee el grafo de un archivo (wrapper de CpgQueryService.read)."""
        from features.code_analysis.application.query_service import QueryGraphUseCase
        from features.project import ProjectService

        paths = ProjectService.resolve_paths(self._chain_dir or "")
        data_dir = str(paths.data_dir) if paths.data_dir else ""
        q = QueryGraphUseCase(
            chain_dir=self._chain_dir or "",
            project_id=self._inner.project_id,
            data_dir=data_dir or None,
        )
        return q.read(file_path, verbose)

    def get_analysis_adapter(self, chain_dir: str) -> Any:
        """Construye el adapter de análisis CPG (ICpgAnalysisPort)."""
        from features.code_analysis import cpg_analysis_adapter

        return cpg_analysis_adapter(chain_dir)

    @staticmethod
    def check_health(project_id: str, data_dir: str) -> dict:
        """Verifica el estado del CPG (wrapper de CheckCpgHealthUseCase)."""
        from features.code_analysis.application.use_cases.check_cpg_health import (
            CheckCpgHealthUseCase,
        )

        return CheckCpgHealthUseCase.execute(data_dir)

    @classmethod
    def from_chain_dir(cls, chain_dir: str) -> "CpgService | None":
        """Construye CpgService desde chain_dir (composition root).

        Args:
            chain_dir: Directorio del sprint.

        Returns:
            CpgService construido o None si no hay contexto resolvible.
        """
        if not chain_dir:
            return None
        from features.project import ProjectService

        paths = ProjectService.resolve_paths(chain_dir)
        project_id = paths.project_id
        data_dir = str(paths.project_root)
        code_root = str(paths.project_root)
        return cls(
            project_id=project_id,
            data_dir=data_dir,
            code_root=code_root,
            chain_dir=chain_dir,
        )

    @classmethod
    def from_project_dir(cls, project_dir: str) -> "CpgService | None":
        """Construye CpgService desde project_dir (composition root).

        Args:
            project_dir: Directorio cognitivo del proyecto (.cognitive/projects/...).

        Returns:
            CpgService construido o None si no hay contexto resolvible.
        """
        if not project_dir:
            return None
        from pathlib import Path
        from features.project import ProjectService

        pdir = Path(project_dir).expanduser().resolve()
        project_id = pdir.name
        manifest = ProjectService.resolve_manifest(pdir)
        if manifest:
            project_id = (
                manifest.get("project", manifest).get("meta", {}).get("project_id", "")
                or project_id
            )
        return cls(
            project_id=project_id,
            data_dir=str(pdir),
            code_root=str(pdir),
        )
