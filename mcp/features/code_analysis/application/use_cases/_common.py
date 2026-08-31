"""use_cases/_common.py — Helpers compartidos de los use cases de code_analysis.

ADR-WALKER-048: estos helpers son IMPLEMENTACIÓN INTERNA del feature
code_analysis. Nadie fuera del módulo debe importarlos.
"""
from __future__ import annotations

from features.code_analysis.application.ports.cpg_repository import CpgRepository


def _resolve_repo(chain_dir_or_repo: str | CpgRepository) -> CpgRepository:
    """Resuelve un CpgRepository desde un chain_dir (legacy) o lo pasa tal cual.

    R1 (ADR-WALKER-055): delega en el ContextResolver compartido
    (app.context_resolver) — una sola aritmética de resolución.
    """
    if isinstance(chain_dir_or_repo, CpgRepository):
        return chain_dir_or_repo
    from features.code_analysis.infrastructure.cognitive_cpg_repo import CognitiveCpgRepository
    from app.path_resolver import resolve_memory_context

    ctx = resolve_memory_context(chain_dir_or_repo)
    return CognitiveCpgRepository(ctx.data_dir, ctx.project_id)
