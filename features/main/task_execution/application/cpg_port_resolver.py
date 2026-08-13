"""task_execution/application/cpg_port_resolver.py — Factory del puerto CPG.

Centraliza la creación de ICpgAnalysisport para las tasks públicas.
Si no se inyecta un puerto, delega en la factory raíz del módulo
code_analysis (cpg_analysis_adapter). Nunca toca infraestructura de
code_analysis directamente: los consumidores importan de la raíz del
paquete (patrón composition root, ver code_analysis/__init__.py).
"""
from __future__ import annotations

from features.main.task_execution.ports.cpg_analysis_port import ICpgAnalysisPort


def resolve_cpg_port(chain_dir: str, port: ICpgAnalysisPort | None = None) -> ICpgAnalysisPort | None:
    """Resuelve el puerto CPG para una task.

    Args:
        chain_dir: directorio del sprint/proyecto.
        port: puerto inyectado (opcional). Si se provee, se usa tal cual.

    Returns:
        ICpgAnalysisPort o None si no se pudo construir.
    """
    if port is not None:
        return port
    from features.code_analysis import cpg_analysis_adapter

    return cpg_analysis_adapter(chain_dir)