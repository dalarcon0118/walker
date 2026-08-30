"""use_cases/check_ckg_health.py — CheckCkgHealthUseCase.

Check CKG health: node count and status.
SSOT: solo cognitive_knowledge consulta el CKG, SIEMPRE por el puerto
(CA-9, ADR-WALKER-063): ninguna lectura directa de `.json.gz`.
Un backend corrupto propaga como `Failure` (IV-6); un índice vacío es
estado válido ("empty"), no un fallo.
"""
from __future__ import annotations

from returns.result import Result, safe


class CheckCkgHealthUseCase:
    """Check CKG health: node count and status.

    SRP: Single source of truth for CKG health data.
    """

    @staticmethod
    def execute(projects_data_dir: str) -> Result[dict, Exception]:
        """Check CKG status in the given data directory.

        Returns:
            Result[dict, Exception] con status y node_count
        """

        @safe
        def _read_ckg() -> dict:
            # CA-9 (ADR-063): lectura por el puerto (backend por config).
            # Con sqlite el conteo viene de la BD; con file, del .json.gz.
            from features.cognitive_knowledge.infrastructure.ckg_reader import (
                read_ckg_via_port,
            )

            data = read_ckg_via_port(projects_data_dir)
            total = len(data.get("nodes", []))
            return {"status": f"{total} nodes", "node_count": total}

        return _read_ckg()