"""shared/core — singletons inyectables (Core).

Regla: se consumen por DI (get_module_service), NUNCA por import directo.
El registro vive en features/engine/bootstrap.py (_register_libs_services).
"""
