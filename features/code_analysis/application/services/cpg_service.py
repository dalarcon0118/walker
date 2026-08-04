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
    ):
        self._repo = repo or CognitiveCpgRepository(data_dir, project_id)
        self._inner = BuildGraphUseCase(
            project_id=project_id,
            data_dir=data_dir,
            code_root=code_root,
            code_paths=code_paths,
            repo_path=repo_path,
        )

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
