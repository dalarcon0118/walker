"""libs/module_system/global_registry.py — Registro global de ModuleManifests.

AUTO-REGISTRO POR IMPORT (ADR-WALKER-068): cada `*/module.py` declara su
`MODULE` y lo auto-registra al final del archivo (`register_manifest(MODULE)`),
igual que `shared/core/engine/bootstrap.py` auto-registra los puertos del
dominio al importar el engine.

El composition root (AppModule) importa los módulos POR REFERENCIA (NestJS
`@Module({imports: [...]})`) — el import dispara el auto-registro. NO hay una
lista central de strings de paths (anti-patrón que se elimina).

Espejo del `ports_registry` del engine: registro global en memoria, re-inyectable
en el ModuleRegistry por instancia (register_all los aplica + valida aciclicidad).
"""
from __future__ import annotations

from typing import Any

# nombre del manifest -> ModuleManifest (auto-registrado al importar cada module.py).
_MANIFESTS: dict[str, Any] = {}


def register_manifest(manifest: Any) -> None:
    """Auto-registro: cada */module.py llama esto al final (al ser importado)."""
    _MANIFESTS[manifest.name] = manifest


def registered_manifests() -> list:
    """Manifiestos auto-registrados (los importa el composition root)."""
    return list(_MANIFESTS.values())


def reset_manifests() -> None:
    """Limpia el registro (hot reload / tests)."""
    _MANIFESTS.clear()