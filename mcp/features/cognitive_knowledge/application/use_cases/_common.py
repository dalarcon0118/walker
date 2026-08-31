"""use_cases/_common.py — Helpers compartidos de los use cases del CKG.

ADR-WALKER-048: estos helpers son IMPLEMENTACIÓN INTERNA del feature
cognitive_knowledge. Nadie fuera del módulo debe importarlos.
"""
from __future__ import annotations


def _get_ckg(chain_dir: str):
    from features.cognitive_knowledge import ckg_service
    from features.project import ProjectService

    paths = ProjectService.resolve_paths(chain_dir)
    return ckg_service(str(paths.project_root))
