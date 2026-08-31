"""shared/libs/module_system — sistema de módulos genérico (manifiestos + DI), ADR-066."""
from shared.libs.module_system.contracts import (
    IModule,
    IModuleRegistry,
    ModuleDefinition,
    ModuleManifest,
    RestRoute,
    ToolDefinition,
)
from shared.libs.module_system.registry import InMemoryModuleRegistry

__all__ = [
    "ToolDefinition",
    "RestRoute",
    "ModuleDefinition",
    "ModuleManifest",
    "IModule",
    "IModuleRegistry",
    "InMemoryModuleRegistry",
]
