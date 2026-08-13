"""shared/router/ — Router reutilizable tipo NestJS/Express para walker CLI.

Permite definir rutas con parametros en el path y middleware pipeline.
Similar a los routers de frameworks REST pero para CLI.

Uso:

    from shared.common.router import Router

    router = Router()

    @router.on(":projectId/status")
    def status_handler(params, flags):
        # params = {"projectId": "walker"}
        # flags = {"--verbose": True}
        ...

    @router.on(":projectId/task/:taskName")
    def task_handler(params, flags):
        # params = {"projectId": "walker", "taskName": "kg_search"}
        ...

    # Middleware global
    router.use(lambda params, flags, next: next())

    # Resolver
    handler, params, flags = router.resolve("walker/task/kg_search")
"""

from .router import Router
