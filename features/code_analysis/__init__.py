"""CPG — Code Property Graph builder + query service.

API publica:
    from features.code_analysis import CPGBuilder, CpgQueryService

Arquitectura DDD + Hexagonal:
    domain/         → Node, Edge, Graph, Query, BugFinding
    application/    → BuildGraphUseCase, QueryGraphUseCase, ScanBugsUseCase
    infrastructure/ → sg_adapter, repository, ast_grep_calls, filters
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class CPGBuilder:
    """Code Property Graph builder.
    
    Lee project.json para resolver paths y delega en BuildGraphUseCase.
    """

    def __init__(self, project_dir: str):
        from features.code_analysis.application.build_service import BuildGraphUseCase
        from features.project import ProjectService

        self.project_dir = Path(project_dir).expanduser().resolve()
        self.project_id: str = self.project_dir.name

        project = ProjectService.from_project_id(self.project_id)
        self.repo_path = project.repo_path
        self.code_paths = project.code_paths
        self.cognitive_subdir = ""

        code_root = self.project_dir
        if self.repo_path and os.path.isdir(self.repo_path):
            code_root = Path(self.repo_path).resolve()

        self.real_project_dir = self.project_dir
        if self.repo_path:
            candidate = Path(self.repo_path) / "projects" / self.project_id
            if candidate.is_dir():
                self.real_project_dir = candidate

        self.code_root = code_root

        # CPG data goes to cognitive dir: .cognitive/projects/<pid>/data/
        cognitive_data = self.project_dir / ".cognitive" / "projects" / self.project_id / "data"
        if not cognitive_data.is_dir():
            cognitive_data = self.project_dir / "data"  # fallback
        self._inner = BuildGraphUseCase(
            project_id=self.project_id,
            data_dir=str(cognitive_data),
            code_root=str(code_root),
            code_paths=self.code_paths,
            repo_path=self.repo_path,
        )

    def _resolve_paths(self, paths: list[str] | None = None) -> list[str]:
        return self._inner.resolve_code_paths(paths)

    def build(self, paths: list[str] | None = None) -> dict:
        return self._inner.build(paths)

    def update(self, refresh: bool = False) -> dict:
        return self._inner.update(refresh)

    def index_task_files(self, task_files: list[str]) -> dict:
        return self._inner.index_task_files(task_files)

    def scan_cfg(self, paths: list[str] | None = None) -> dict:
        return self._inner.scan_cfg(paths)

    def scan_dfg(self, paths: list[str] | None = None) -> dict:
        return self._inner.scan_dfg(paths)

    def scan_dfg_async(self, paths: list[str] | None = None) -> dict:
        """Ejecuta el DFG security scan en background (no bloquea al agente).

        Persiste data_dir/dfg_scan_status.json consultable vía dfg_scan_status().
        """
        return self._inner.scan_dfg_async(paths)

    def dfg_scan_status(self) -> dict:
        """Estado del DFG scan asíncrono (running | ok | failed | not_started)."""
        return self._inner.dfg_scan_status()

    def stats(self) -> dict:
        from features.code_analysis.infrastructure.repository import CpgFileRepository
        repo = CpgFileRepository(str(self.project_dir / "data"))
        data = repo.load(self.project_id)
        return {
            "total_nodes": len(data.get("nodes", {})),
            "total_edges": len(data.get("edges", [])),
            "generated_at": data.get("generated_at", ""),
            "updated_at": data.get("updated_at", ""),
            "update_count": data.get("update_count", 0),
        }


class CpgQueryService:
    """Query service for the Code Property Graph.
    
    Delega en QueryGraphUseCase + ScanBugsUseCase.
    """

    def __init__(self, chain_dir: str):
        from features.code_analysis.application.query_service import QueryGraphUseCase
        from features.project import ProjectService

        self.chain_dir = chain_dir
        paths = ProjectService.resolve_paths(chain_dir)
        self._project_root = paths.project_root
        self._project_id = paths.project_id
        
        data_dir = paths.data_dir if paths.data_dir else ""
        self._inner = QueryGraphUseCase(
            chain_dir=chain_dir,
            project_id=self._project_id,
            data_dir=data_dir if os.path.isdir(data_dir) else None,
        )

    @property
    def _nodes(self):
        return {nid: n.to_dict() for nid, n in self._inner.graph.nodes.items()}

    @property
    def _edges(self):
        return self._inner.graph.edges

    @property
    def _outgoing(self):
        return self._inner.graph.outgoing

    @property
    def _incoming(self):
        return self._inner.graph.incoming

    @property
    def _calls_set(self):
        edges = self._inner.graph.edges
        result = set()
        for e in edges:
            if isinstance(e, dict) and e.get("type") == "CALLS":
                result.add((e.get("from", ""), e.get("to", "")))
            elif hasattr(e, 'type') and e.type == "CALLS":
                result.add((e.from_id, e.to_id))
        return result

    def _load(self, force_refresh: bool = False) -> None:
        if force_refresh:
            self._inner.clear_cache()
        _ = self._inner.graph

    def clear_cache(self) -> None:
        self._inner.clear_cache()

    def parse_query(self, query: str):
        from features.code_analysis.domain.query import Query as Q
        q = Q.parse(query)
        return [(f.key, f.value) for f in q.filters], q.free_text

    def grep(self, query: str) -> dict:
        return self._inner.grep(query)

    def read(self, file_path: str, verbose: bool = False) -> dict:
        return self._inner.read(file_path, verbose)
