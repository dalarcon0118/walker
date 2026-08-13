"""
router.py — Router reutilizable estilo Express/NestJS con patrones de path
y middleware pipeline.

Inspirado en NestJS/Express:
  - Rutas con parametros: :projectId, :taskName
  - Verbos HTTP: GET, POST, PUT, PATCH, DELETE (router.get/post/...)
  - Middleware pipeline global (router.use) y por-ruta (router.use_on)
  - Path matching con extraccion de parametros
  - Priorización por especificidad (exacto > menos params)
  - Handler recibe (params, flags, context)

Uso:

    router = Router()

    # Registrar rutas con verbos HTTP
    router.get(":projectId/status", status_handler)
    router.post(":projectId/submit", submit_handler)
    router.add("list", list_handler)

    # Middleware global
    router.use(auth_middleware)
    # Middleware por-ruta
    router.use_on(":projectId/status", resolve_project)

    # Resolver
    handler, params, flags, matched = router.resolve("walker/status", method="GET")
    # → handler=status_handler, params={"projectId": "walker"}

    # Decorador
    @router.on(":projectId/task/:taskName")
    def handler(params, flags):
        ...
"""

from __future__ import annotations
import re
from typing import Any, Callable


class Route:
    """Ruta registrada con patron, handler, métodos HTTP y metadata."""

    def __init__(
        self,
        pattern: str,
        handler: Callable,
        metadata: dict | None = None,
        methods: set[str] | None = None,
    ):
        self.pattern = pattern
        self.handler = handler
        self.metadata = metadata or {}
        self.methods: set[str] = methods or {"GET"}
        self.middleware: list[Callable] = []
        self._regex, self._param_names = self._compile(pattern)

    def add_middleware(self, fn: Callable) -> None:
        """Agrega middleware específico de esta ruta (se ejecuta tras el global)."""
        self.middleware.append(fn)

    def match(self, path: str) -> dict[str, str] | None:
        """Intenta matchear un path contra el patron.

        Returns:
            Dict con parametros extraidos, o None si no matchea.
        """
        m = self._regex.match(path)
        if not m:
            return None
        return {name: m.group(name) for name in self._param_names}

    @staticmethod
    def _compile(pattern: str) -> tuple[re.Pattern, list[str]]:
        """Compila un patron tipo :param a regex con grupos nombrados.

        ":projectId/task/:taskName" → regex: (?P<projectId>[^/]+)/task/(?P<taskName>[^/]+)
        """
        param_names = []
        parts = pattern.strip("/").split("/")
        regex_parts = []
        for part in parts:
            if part.startswith(":"):
                name = part[1:]
                param_names.append(name)
                regex_parts.append(f"(?P<{name}>[^/]+)")
            else:
                regex_parts.append(re.escape(part))
        regex_str = "^" + "/".join(regex_parts) + "$"
        return re.compile(regex_str), param_names

    def __repr__(self) -> str:
        return f"<Route pattern={self.pattern!r}>"


class Router:
    """Router de paths estilo REST con middleware pipeline.

    Almacena rutas registradas y las matchea contra paths entrantes.
    Soporta middleware que se ejecutan antes de cada handler.
    """

    def __init__(self):
        self._routes: list[Route] = []
        self._middleware: list[Callable] = []
        self._global_metadata: dict = {}

    # ── Registro de rutas ──

    def add(
        self,
        pattern: str,
        handler: Callable,
        metadata: dict | None = None,
        methods: set[str] | None = None,
    ) -> Route:
        """Registra una ruta.

        Args:
            pattern: Patron del path (ej: ":projectId/status")
            handler: Funcion handler(params, flags, context=None)
            metadata: Metadata opcional (descripcion, tags, etc.)
            methods: Set de verbos HTTP ("GET", "POST", ...). Default {"GET"}.

        Returns:
            La ruta registrada.
        """
        route = Route(pattern, handler, metadata, methods)
        self._routes.append(route)
        return route

    # ── Verbos HTTP (estilo Express/NestJS) ──
    def get(self, pattern: str, handler: Callable, metadata: dict | None = None) -> Route:
        return self.add(pattern, handler, metadata, methods={"GET"})

    def post(self, pattern: str, handler: Callable, metadata: dict | None = None) -> Route:
        return self.add(pattern, handler, metadata, methods={"POST"})

    def put(self, pattern: str, handler: Callable, metadata: dict | None = None) -> Route:
        return self.add(pattern, handler, metadata, methods={"PUT"})

    def patch(self, pattern: str, handler: Callable, metadata: dict | None = None) -> Route:
        return self.add(pattern, handler, metadata, methods={"PATCH"})

    def delete(self, pattern: str, handler: Callable, metadata: dict | None = None) -> Route:
        return self.add(pattern, handler, metadata, methods={"DELETE"})

    def any(self, pattern: str, handler: Callable, metadata: dict | None = None) -> Route:
        return self.add(pattern, handler, metadata, methods={"GET", "POST", "PUT", "PATCH", "DELETE"})

    def on(self, pattern: str, metadata: dict | None = None):
        """Decorador para registrar una ruta.

        Uso:
            @router.on(":projectId/status")
            def handler(params, flags):
                ...
        """
        def decorator(handler):
            self.add(pattern, handler, metadata)
            return handler
        return decorator

    # ── Middleware ──

    def use(self, fn: Callable) -> None:
        """Registra middleware global.

        El middleware recibe (params, flags, next, context) y debe
        llamar a next() para continuar al siguiente middleware/handler.

        Uso:
            def logger(params, flags, next, context):
                print(f"→ {params}")
                next()
        """
        self._middleware.append(fn)

    def use_on(self, pattern: str, fn: Callable) -> None:
        """Registra middleware específico de una ruta (estilo Express router-level).

        Se ejecuta después de los middleware globales y antes del handler
        de la ruta que coincida con el pattern.
        """
        route = self._find(pattern)
        if route is not None:
            route.add_middleware(fn)

    # ── Resolucion ──

    def _find(self, pattern: str) -> Route | None:
        """Busca una ruta registrada por pattern (primer match)."""
        for r in self._routes:
            if r.pattern == pattern:
                return r
        return None

    def resolve(
        self,
        path: str,
        flags: dict[str, str] | None = None,
        method: str | None = None,
    ) -> tuple[Callable | None, dict[str, str], dict[str, str], list[Route]]:
        """Resuelve un path contra las rutas registradas.

        Args:
            path: Path a resolver (ej: "walker/task/kg_search")
            flags: Flags adicionales del CLI (ej: {"index": "delta"})
            method: Verbo HTTP ("GET", "POST", ...). Si es None, ignora
                    el verbo y matchea solo por path (estilo CLI simple).

        Returns:
            Tuple de (handler, params, flags, matched_routes)
            handler puede ser None si no hay match.
            matched_routes incluye todas las rutas que matchearon
            (para mostrar ayuda si hay multiples).
        """
        clean_path = path.strip("/")
        matched: list[tuple[Route, dict[str, str]]] = []

        for route in self._routes:
            params = route.match(clean_path)
            if params is None:
                continue
            if method is not None and method not in route.methods:
                continue
            matched.append((route, params))

        if not matched:
            return None, {}, flags or {}, []

        # Priorizar la ruta más específica: exacto (0 params) > menos params.
        # Ej: "task/kg_schema" matchea "task/:taskName" (1 param) y
        #     ":projectId/task/:taskName" (2 params) → gana la de 1 param.
        best = min(matched, key=lambda rp: len(rp[1]))

        route, params = best
        return route.handler, params, flags or {}, matched

    def resolve_all(
        self,
        path: str,
        flags: dict[str, str] | None = None,
    ) -> list[tuple[Route, dict[str, str]]]:
        """Resuelve un path y retorna TODOS los matches (no solo el mejor)."""
        clean_path = path.strip("/")
        matched = []
        for route in self._routes:
            params = route.match(clean_path)
            if params is not None:
                matched.append((route, params))
        return matched

    # ── Ejecucion ──

    def dispatch(
        self,
        path: str,
        flags: dict[str, str] | None = None,
        context: Any = None,
        method: str | None = None,
    ) -> Any:
        """Resuelve y ejecuta el handler con middleware pipeline.

        Args:
            path: Path a resolver
            flags: Flags del CLI
            context: Contexto opcional (ej: ExecutionContext)
            method: Verbo HTTP opcional

        Returns:
            Lo que retorne el handler.
        """
        handler, params, resolved_flags, matched = self.resolve(path, flags, method)
        if handler is None:
            return None

        # Encontrar la ruta elegida (la mejor) para su middleware específico
        best_route = None
        for route, p in matched:
            if route.handler is handler:
                best_route = route
                break
        if best_route is None:
            best_route = matched[0][0] if matched else None

        return self._run_pipeline(handler, params, resolved_flags, context, best_route)

    def _run_pipeline(
        self,
        handler: Callable,
        params: dict[str, str],
        flags: dict[str, str],
        context: Any = None,
        route: Route | None = None,
    ) -> Any:
        """Ejecuta middleware global → middleware de ruta → handler.

        El resultado del handler siempre se propaga hacia arriba, incluso si
        un middleware llama next() sin `return next()` (patrón Express común).
        """
        all_mw = list(self._middleware) + (list(route.middleware) if route else [])
        if not all_mw:
            return handler(params, flags, context)

        idx = 0
        result_holder: dict[str, Any] = {}

        def invoke_handler() -> Any:
            value = handler(params, flags, context)
            result_holder["value"] = value
            return value

        def next_middleware():
            nonlocal idx
            if idx < len(all_mw):
                fn = all_mw[idx]
                idx += 1
                return fn(params, flags, next_middleware, context)
            return invoke_handler()

        next_middleware()
        return result_holder.get("value")

    # ── Utilidades ──

    def list_routes(self) -> list[dict]:
        """Retorna todas las rutas registradas para autodocumentacion."""
        return [
            {
                "pattern": r.pattern,
                "params": r._param_names,
                "methods": sorted(r.methods),
                "metadata": r.metadata,
            }
            for r in self._routes
        ]

    def __repr__(self) -> str:
        return f"<Router routes={len(self._routes)} middleware={len(self._middleware)}>"
