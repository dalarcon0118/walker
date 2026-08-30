"""tasks/module.py — Manifiesto de la capa task_execution (ADR-066 F3).

Declara el módulo de tasks. El REGISTRO de los handlers en el task_registry /
ActionCatalog lo hace `app/module.py` (composition root declarativo, ADR-067
§2.4) en su hook `register`, contra los providers del ModuleRegistry — no aquí.
No implementa puertos del dominio: es infraestructura de ejecución de tareas.
"""
from __future__ import annotations

from shared.libs.module_system.contracts import ModuleManifest


MODULE = ModuleManifest(
    name="tasks",
    imports=["shared"],
    providers=[],
    adapters=[],
    exports=["register_all"],
)
