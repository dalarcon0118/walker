"""shared — Servicios transversales del Walker (taxonomía NestJS/DDD).

La antigua `shared/` se reorganizó en dos roles con responsabilidad declarada,
siguiendo la taxonomía de módulos de NestJS (Core vs Shared):

  shared/core/    → singletons inyectables (con estado o instancia única).
                    Se consumen por DI: get_module_service("clave").
                    NUNCA por import directo (regla "Core se inyecta").
                    Contiene: cognitive_graph, path_resolver, mcp_server,
                    cognitive_searcher, memories (fachada de stats).
  shared/common/  → puros importables (sin estado, sin IO).
                    Se consumen por import directo (regla "Shared se importa").
                    Contiene: shared_domain (VOs), result_types,
                    context (contratos DIP), router.

REGLAS DE MÓDULO (definición formal):
  1. shared NO importa features/. Solo stdlib + shared entre sí (DIP).
  2. Cada paquete tiene UNA responsabilidad transversal (no lógica de negocio).
  3. Si algo pertenece a un feature (cpg, ckg, delta, trace, spring) o tiene
     lógica de negocio (transiciones, gates, schemas de tasks), vive en
     features/<feature>/ y NO en shared/. La lógica del workflow v4 legacy
     (antigua shared/infrastructure/utils.py) se migró a features/engine/legacy/.
  4. Los events de dominio viven en features/<feature>/domain/events.py.
  5. Lo que se consume por DI (Core) se registra en el ModuleRegistry
     (features/engine/bootstrap.py) — nunca se importa directo.
"""
