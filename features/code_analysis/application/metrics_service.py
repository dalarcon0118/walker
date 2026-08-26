"""metrics_service.py — Métricas CPG-nativas SIN binario externo (ADR-WALKER-053).

Elimina la dependencia de `qlty init` por proyecto y los ceros silenciosos:
todo se deriva del MISMO build del grafo (nodes ya indexados) + lectura de
archivos para LOC. Regla de estilo: el dominio devuelve value objects
(ProjectMetrics); la serialización a dict ocurre UNA vez en el borde de
salida (task inspect_codebase), nunca en el puerto/adaptador.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from returns.result import Result, Success, Failure

logger = logging.getLogger(__name__)

from features.code_analysis.domain.metrics import ProjectMetrics, FileMetrics
from features.code_analysis.infrastructure.repository import CpgFileRepository

from features.code_analysis.domain.graph import CLASS_TYPES, FUNCTION_TYPES
from features.code_analysis.infrastructure.ast_grep_adapter import _LANG_MAP

MAX_LOC_FILES = 8000  # guard-rail de coste (no es una suposición de lenguaje)


@dataclass(frozen=True)
class MetricsReport:
    """Value object de salida: métricas + advertencias visibles."""

    metrics: ProjectMetrics
    source: str = "cpg-native"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = self.metrics.to_dict()
        d["source"] = self.source
        if self.warnings:
            d["warnings"] = self.warnings
        return d


def _normalize_scope(scope: str, repo_path: str) -> str:
    """Ruta absoluta → relativa al repo (los nodos del CPG guardan rutas relativas).

    Este era el mismatch que producía files:0 en los analizadores viejos.
    """
    scope = (scope or "").strip()
    if not scope:
        return ""
    if os.path.isabs(scope):
        try:
            rel = os.path.relpath(scope, repo_path)
        except ValueError:
            return "\0impossible"
        if rel.startswith(".."):
            return "\0impossible"
        if rel in (".", "./"):
            return ""          # raíz del repo = SIN filtro
        return rel.replace(os.sep, "/")
    return scope.replace(os.sep, "/")


def _count_loc(repo_path: str, scope_rel: str, file_paths: list[str]) -> tuple[int, int]:
    """(files, loc) a partir de los PROPIOS file-nodes del CPG.

    El indexador ya decidió qué es código (_LANG_MAP, 24 lenguajes): no se
    vuelve a adivinar por extensión. Fallback walk SOLO si el grafo no trajo
    file-nodes en el alcance; usa las extensiones del SSOT _LANG_MAP.
    """
    base = os.path.join(repo_path, scope_rel) if scope_rel else repo_path
    if file_paths:
        n_files = 0
        loc = 0
        for rel in file_paths[:MAX_LOC_FILES]:
            fp = os.path.join(repo_path, rel)
            try:
                with open(fp, "rb") as fh:
                    loc += sum(1 for _ in fh)
                n_files += 1
            except OSError as exc:
                logger.warning("LOC: archivo ilegible %s (%s)", fp, exc)
        return n_files, loc

    # fallback: walk con extensiones canónicas del indexador
    code_exts = set(_LANG_MAP.keys())
    skip_dirs = {"node_modules", ".git", ".cognitive", ".qlty", "dist", "build", ".expo"}
    n_files = 0
    loc = 0
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            if os.path.splitext(f)[1].lower() not in code_exts:
                continue
            n_files += 1
            if n_files > MAX_LOC_FILES:
                return n_files, loc
            try:
                with open(os.path.join(root, f), "rb") as fh:
                    loc += sum(1 for _ in fh)
            except OSError:
                continue
    return n_files, loc


def compute_cpg_metrics(
    repo_path: str, data_dir: str, scope: str = ""
) -> Result[MetricsReport, str]:
    """Métricas agregadas desde el grafo ya construido (mismo build del CPG)."""
    if not repo_path:
        return Failure("repo_path vacío — sin contexto de proyecto")

    scope_rel = _normalize_scope(scope, repo_path)

    try:
        graph = CpgFileRepository(data_dir).load_graph()
    except Exception as exc:
        return Failure(f"no se pudo cargar el CPG ({exc}); ejecutá build_code_index")

    warnings: list[str] = []
    by_type: dict[str, int] = {}
    per_func_file: dict[str, int] = {}
    file_node_paths: list[str] = []

    for node in getattr(graph, "nodes", {}).values():
        npath = (getattr(node, "path", "") or "").replace(os.sep, "/")
        if scope_rel and not npath.startswith(scope_rel):
            continue
        ntype = getattr(node, "type", "") or "unknown"
        by_type[ntype] = by_type.get(ntype, 0) + 1
        if ntype == "file" and npath:
            file_node_paths.append(npath)
        if ntype in FUNCTION_TYPES:
            per_func_file[npath] = per_func_file.get(npath, 0) + 1

    scoped_files = [
        p for p in file_node_paths if not scope_rel or p.startswith(scope_rel)
    ]
    fs_files, loc = _count_loc(repo_path, scope_rel, scoped_files)

    total_functions = sum(c for t, c in by_type.items() if t in FUNCTION_TYPES)
    files_in_cpg = by_type.get("file", 0)

    # LOC del disco manda si el walk cubrió más que el índice de files
    files_total = max(fs_files, files_in_cpg)
    if fs_files > MAX_LOC_FILES:
        warnings.append(f"loc truncada a {MAX_LOC_FILES} archivos")

    per_file = [
        FileMetrics(file=f, functions=c)
        for f, c in sorted(per_func_file.items(), key=lambda kv: -kv[1])[:10]
    ]

    metrics = ProjectMetrics(
        total_files=files_total,
        total_loc=loc,
        total_functions=total_functions,
        total_classes=sum(c for t, c in by_type.items() if t in CLASS_TYPES),
        total_imports=by_type.get("module", 0) + by_type.get("constant", 0),
        cyclo_avg=0.0,
        cyclo_max=0,
        dead_code_count=by_type.get("dead", 0),
        security_findings=0,
        files=per_file,
    )

    report = MetricsReport(
        metrics=metrics,
        warnings=[
            "cyclomatic complexity no disponible en modo CPG-native "
            "(requeriría análisis AST profundo); gates de complejidad omitidos"
        ]
        + warnings,
    )
    return Success(report)
