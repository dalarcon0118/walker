"""infrastructure/ckg_reader.py — Lectura del CKG SOLO por el puerto (CA-9).

ADR-WALKER-063 CA-9: ninguna lectura directa de `.json.gz` en features.
Los lectores del CKG consumen la facade `CognitiveGraph` con el backend
resuelto por config (`file`/`.json.gz` → `sqlite` v2). El storage de fondo
es transparente al lector: con `WALKER_MEMORY_BACKEND=sqlite` el mismo
servicio responde desde SQLite sin tocar una línea del lector.

Formato devuelto (equivalente al blob histórico):
  {"nodes": [...], "edges": [{"from", "to", ...}]}
Los nodos son planos (sin clave interna `_id`; keyed por id externo),
exactamente lo que consumían los lectores legacy.
"""

from __future__ import annotations


def ckg_data_dir(project_root: str, ckg_path: str = "") -> str:
    """Resuelve el directorio de datos del CKG (jerarquía del layout).

    Prioridad: dirname del ckg_path (si viene) > <project_root>/data.
    """
    if ckg_path:
        import os

        return os.path.dirname(os.path.abspath(ckg_path))
    return f"{project_root}/data".replace("//", "/")


def read_ckg_via_port(data_dir: str) -> dict:
    """Lee nodos+edges del CKG por el puerto (backend por config).

    Args:
        data_dir: directorio de datos del proyecto (<project_root>/data).

    Returns:
        {"nodes": [...], "edges": [...]}. Índice vacío/ausente →
        {"nodes": [], "edges": []} (shape explícito, IV-7). Un backend
        corrupto (IntegrityError) frente a SQLite propaga ruidoso (IV-6);
        un índice inexistente NO es un fallo: es el estado "sin CKG aún".
    """
    from shared.libs.cognitive_graph import CognitiveGraph
    from shared.libs.cognitive_graph.config import load_memory_connection

    conn = load_memory_connection(base_dir=data_dir)
    cg = CognitiveGraph(conn=conn)  # register_defaults → registro de "ckg"
    nodes = cg.get_docs("ckg") or {}
    edges = cg.get_all_edges("ckg") or []
    return {"nodes": list(nodes.values()), "edges": list(edges)}


__all__ = ["ckg_data_dir", "read_ckg_via_port"]