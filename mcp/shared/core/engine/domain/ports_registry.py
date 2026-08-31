"""domain/ports_registry.py — Registro local de puertos del dominio.

El dominio (domain/) NO puede importar infrastructure/ ni application/ (regla
domain-first / clean architecture). Para resolver esa dependencia hacia arriba
sin acoplarse, el dominio define PUERTOS en `ports.py` y los RESUELVE en runtime
desde este registro local.

El composition root (app/composition/registry.py vía app/bootstrap.py, o el propio
shared/core/engine/bootstrap.py) registra las implementaciones concretas (los
adaptadores de infrastructure/application) AQUI. Así:

  domain/state.py  --(resolve_port)--> IStateStore  <--(registrado)--  WorkflowStateRepository (infra)

Sin que domain/ importe nunca infrastructure/ ni application/.
"""
from __future__ import annotations

from typing import Any

# Registro en memoria: tipo_del_puerto -> instancia_adaptador.
_PORTS: dict[type, Any] = {}


def register_port(port_type: type, instance: Any) -> None:
    """Registra la implementacion concreta de un puerto del dominio."""
    _PORTS[port_type] = instance


def resolve_port(port_type: type) -> Any:
    """Resuelve la implementacion de un puerto, o None si no esta registrado."""
    return _PORTS.get(port_type)


def reset_ports() -> None:
    """Limpia el registro (hot reload / tests)."""
    _PORTS.clear()
