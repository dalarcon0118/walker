"""application/services/code_analysis_service.py — CodeAnalysisService.

Application Service del feature code_analysis (patrón walker — ADR-048/INV-013):
UNA interfaz hacia el exterior (composition root, CLI, MCP) que orquesta los
use cases del feature. Igual que CkgService/SpringService.

Consumidores externos: usan ESTE servicio, NUNCA los use cases directos.
"""

from __future__ import annotations

from typing import Any


class CodeAnalysisService:
    """Application Service: análisis de código (CPG) — interfaz única."""

    def __init__(self, chain_dir: str = "", project_id: str = ""):
        self._chain_dir = chain_dir
        self._project_id = project_id

    # ── Contexto ────────────────────────────────────────────────────

    def gather_context(self, files: list[str] | None = None) -> dict[str, Any]:
        """Contexto CPG por archivos o estructura general (GatherCpgContextUseCase)."""
        from fast_mcp.features.code_analysis.application.use_cases.gather_cpg_context import (
            GatherCpgContextUseCase,
        )

        return GatherCpgContextUseCase.execute(self._chain_dir, files=files)

    def business_layer(self, focus: str = "") -> dict[str, Any]:
        """Capa de negocio del CPG: pure functions, flows, patrones.

        Delega en QueryGraphUseCase.business_layer (derivado on-the-fly).
        """
        from fast_mcp.features.code_analysis.application.query_service import QueryGraphUseCase
        from fast_mcp.shared_kernel.project_support import ProjectService

        # R-A fix: data_dir canónico explícito (ADR-054 D4) — el guessing
        # de QueryGraphUseCase.repo resolvía <repo_root>/data, no la memoria.
        paths = ProjectService.resolve_paths(self._chain_dir)
        uc = QueryGraphUseCase(
            self._chain_dir,
            project_id=self._project_id or (paths.project_id or ""),
            data_dir=str(paths.data_dir) if paths.data_dir else None,
        )
        return uc.business_layer(focus=focus)

    # ── Bugs / Security (CFG/DFG) ───────────────────────────────────

    def find_bugs(self, bug_type: str = "taint_paths", file_filter: str = "") -> dict[str, Any]:
        """Detección de bugs vía CFG/DFG (bug:* del DSL — ScanBugsUseCase).

        Tipos: dead_branches, missing_returns, infinite_loops,
        uninitialized_vars, taint_paths, data_gaps, unused_functions.
        """
        from fast_mcp.features.code_analysis.application.query_service import QueryGraphUseCase
        from fast_mcp.shared_kernel.project_support import ProjectService

        paths = ProjectService.resolve_paths(self._chain_dir)
        uc = QueryGraphUseCase(
            self._chain_dir,
            project_id=self._project_id or (paths.project_id or ""),
            data_dir=str(paths.data_dir) if paths.data_dir else None,
        )
        q = f"bug:{bug_type}"
        if file_filter:
            q += f" file:{file_filter}"
        return uc.grep(q)

    def security_scan(self, vuln_type: str = "all", file_filter: str = "") -> dict[str, Any]:
        """Security scan vía taint analysis DFG (security:* — ScanBugsUseCase).

        Tipos: sqli, command_injection, xss, code_injection, data_leak,
        weak_crypto, path_traversal, hardcoded_secrets, spoofing, all.
        """
        from fast_mcp.features.code_analysis.application.query_service import QueryGraphUseCase
        from fast_mcp.shared_kernel.project_support import ProjectService

        paths = ProjectService.resolve_paths(self._chain_dir)
        uc = QueryGraphUseCase(
            self._chain_dir,
            project_id=self._project_id or (paths.project_id or ""),
            data_dir=str(paths.data_dir) if paths.data_dir else None,
        )
        q = f"security:{vuln_type}"
        if file_filter:
            q += f" file:{file_filter}"
        return uc.grep(q)

    # ── Dead code ───────────────────────────────────────────────────

    def detect_unused_functions(self, file_filter: str = "") -> dict[str, Any]:
        """Funciones sin callers en el CPG (DetectUnusedFunctionsUseCase)."""
        from fast_mcp.features.code_analysis.application.use_cases.detect_unused_functions import (
            DetectUnusedFunctionsUseCase,
        )

        return DetectUnusedFunctionsUseCase.execute(
            self._chain_dir, file_filter=file_filter
        )

    # ── Review ──────────────────────────────────────────────────────

    def review_code(
        self,
        files: list[str] | None = None,
        thresholds: dict | None = None,
    ) -> dict[str, Any]:
        """Code review CPG-native + bug_proposals (ReviewCodeUseCase)."""
        from fast_mcp.features.code_analysis.application.use_cases.review_code import (
            ReviewCodeUseCase,
        )

        return ReviewCodeUseCase.execute(
            self._chain_dir, files=files, thresholds=thresholds
        )

    # ── Métricas ────────────────────────────────────────────────────

    def project_metrics(self, file_filter: str = "") -> dict[str, Any]:
        """Métricas agregadas del proyecto (ProjectMetricsUseCase)."""
        from fast_mcp.features.code_analysis.application.use_cases.project_metrics import (
            ProjectMetricsUseCase,
        )

        return ProjectMetricsUseCase.execute(self._chain_dir, file_filter=file_filter)

    # ── Convenciones ────────────────────────────────────────────────

    def check_conventions(
        self,
        languages: list[str] | None = None,
        ckg: Any | None = None,
    ) -> dict[str, Any]:
        """Convenciones del CKG contra el código (conventions.execute).

        `ckg` se inyecta desde el composition root (regla §3bis R1): este
        feature no importa cognitive_knowledge.
        """
        from fast_mcp.features.code_analysis.application.conventions import (
            execute as _check_conventions,
        )

        report, errors = _check_conventions(
            self._chain_dir, languages=languages, ckg=ckg
        )
        report["errors"] = errors
        return report