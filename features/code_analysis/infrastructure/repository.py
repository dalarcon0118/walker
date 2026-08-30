"""infrastructure/repository.py — Fábricas del puerto CPG (ADR-WALKER-063).

CA-9: ELIMINADO `CpgFileRepository` (lector/escritor directo del `.json.gz`).
Todo acceso al CPG pasa por el puerto StorageBackend (`CognitiveCpgRepository`,
storage config-driven — sqlite v2 es SSOT). `file_io` queda solo para
refs/export/migración (ADR-WALKER-063 §6.1).

Este módulo conserva únicamente:
  - `CPG_DERIVED_KEYS` — proyecciones que NO viven en el SSOT gráfico
    (ADR-WALKER-055 F3): son archivos *.json o recomputables on-the-fly.
  - `make_cpg_fingerprint_reader` — factory DI del lector de fingerprint
    para el FileWatcher (ADR-WALKER-055 F1), ahora por el puerto.
"""
from __future__ import annotations

# Versión del formato del índice cpg (metadata 'version' del grafo v2).
CPG_VERSION = "2.0"

# Derivados que NO viven en el SSOT gráfico (ADR-WALKER-055 F3): son
# proyecciones (archivos *.json) o recomputables on-the-fly.
# Re-export desde el módulo hoja (cpg_constants) — rompe el ciclo
# repository ↔ cognitive_cpg_repo (ADR-068).
from .cpg_constants import CPG_DERIVED_KEYS


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_cpg_fingerprint_reader(data_dir: str):
    """Factory del reader de fingerprint del CPG (para FileWatcher, DI).

    ADR-WALKER-055 F1: features/project (watcher) NO conoce el formato del
    CPG; el composition root le inyecta este reader provisto por el feature
    que posee el dominio cpg. ADR-WALKER-063 CA-9: lee por el puerto
    (sqlite v2 SSOT), jamás del `.json.gz` legacy.
    """
    from features.code_analysis.infrastructure.cognitive_cpg_repo import (
        CognitiveCpgRepository,
    )

    def _read() -> str:
        return CognitiveCpgRepository(data_dir, "").read_fingerprint()

    return _read