"""shared/core/projection_store.py — ProjectionStore (R4, ADR-WALKER-055 F3).

Primitiva compartida para leer/escribir PROYECCIONES de derivados: archivos
*.json en el data_dir que viven FUERA del SSOT gráfico (quality_report.json,
bug_proposals.json, dfg.json, analysis_results.json, business_layer.json...).

Motivación (patrón R4): la lectura/escritura de proyecciones se repetía en
los features (_load_projection, _write_json_file, _persist_quality_report...).
Fail-open con log (convention-001) — nunca except silencioso.
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)


class ProjectionStore:
    """Lectura/escritura de proyecciones de derivados en data_dir."""

    def __init__(self, data_dir: str):
        self._dir = data_dir

    def _path(self, name: str) -> str:
        return os.path.join(self._dir, name)

    def exists(self, name: str) -> bool:
        return os.path.isfile(self._path(name))

    def read(self, name: str) -> dict:
        """Lee una proyección JSON. Fail-open: {} si falta o está corrupta."""
        path = self._path(name)
        if not os.path.isfile(path):
            return {}
        try:
            with open(path) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "Proyección %s ilegible (%s: %s); se devuelve {}",
                path, type(exc).__name__, exc,
            )
            return {}

    def write(self, name: str, payload: dict) -> None:
        """Escribe una proyección JSON (best-effort atómico, log ante fallo)."""
        if not isinstance(payload, dict):
            raise TypeError(f"proyección {name}: esperaba dict, obtuvo {type(payload).__name__}")
        try:
            os.makedirs(self._dir, exist_ok=True)
            path = self._path(name)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError as exc:
            logger.error(
                "No se pudo persistir proyección %s (%s: %s)",
                name, type(exc).__name__, exc,
            )