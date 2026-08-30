"""Context Composer — orquestador de composición de contexto.

Recibe la declaración de contexto del nodo (context.sources + context.focus),
obtiene los providers del registry, recolecta datos, gestiona el budget,
y ensambla el mensaje estructurado para el LLM.

ContextProviders (task, state, diff, test_results, cpg, ckg, trace, delta,
project) se registran en build_context_registry().
"""
from __future__ import annotations

import json
import logging
import re as _re
from typing import Any

from shared.common.context.models import ContextPiece, ComposedContext, ContextBudget
from .registry import ContextProviderRegistry
from .query_manager import QueryManager
from .window import ContextWindowManager

logger = logging.getLogger(__name__)


def _hash_config(config: dict) -> int:
    """Hash determinista de la config de un provider para caché."""
    return hash(json.dumps(config, sort_keys=True, default=str))


class ContextComposer:
    """Orquestador de composición de contexto.

    Uso:
        composer = ContextComposer(registry)
        result = composer.compose(node_config, prompt_template, ctx)
    """

    def __init__(
        self,
        registry: ContextProviderRegistry,
        window_manager: ContextWindowManager | None = None,
    ):
        self._registry = registry
        self._window = window_manager or ContextWindowManager()
        # Caché de resultados de collect() para evitar re-queries en refine loops.
        # Clave: (provider_name, frozenset(config.items()), task_id, chain_dir)
        # Se invalida vía invalidate_cache() cuando corren side_effects como update_code_index.
        self._collect_cache: dict[tuple, ContextPiece | None] = {}

    # ── API pública ────────────────────────────────────────────────

    def compose(
        self,
        node_config: dict[str, Any],
        prompt_template: str,
        ctx: Any,
    ) -> ComposedContext:
        """Compone el contexto para un nodo.

        Args:
            node_config: el bloque "context" del .node.json
                { sources: [...], focus: {...} }
            prompt_template: string del template .prompt.json con {placeholders}
            ctx: ExecutionContext con .chain_dir, .task_id, .baggage

        Returns:
            ComposedContext listo para renderizar al LLM.
        """
        focus = node_config.get("focus", {})
        sources = node_config.get("sources", [])

        # 1. Recolectar piezas de contexto desde los providers
        pieces = self._collect_pieces(sources, ctx)

        # 2. Gestionar budget
        budget_config = node_config.get("budget", None)
        if budget_config:
            self._window.budget = ContextBudget(**budget_config)
        allocated = self._window.allocate(pieces)

        # 3. Construir system_message desde focus
        system_parts = []

        role = focus.get("role", "")
        if role:
            # Resolver placeholders en el role
            role = self._resolve_placeholders(role, allocated.included, ctx)
            system_parts.append(f"You are {role}.")

        constraints = focus.get("constraints", [])
        if constraints:
            resolved_constraints = [
                self._resolve_placeholders(c, allocated.included, ctx)
                for c in constraints
            ]
            system_parts.append("Constraints:\n" + "\n".join(
                f"• {c}" for c in resolved_constraints
            ))

        context_priority = focus.get("context_priority", "")
        if context_priority:
            system_parts.append(
                f"Context priority order: {context_priority}"
            )

        # 4. Añadir instructions de cada provider incluido
        for piece in allocated.included:
            if piece.instructions:
                system_parts.append(
                    f"\n=== {piece.provider} layer ===\n{piece.instructions}"
                )

        system_message = "\n\n".join(system_parts)

        # 5. Armar context_layers desde el content de cada provider
        context_layers = []
        for piece in allocated.included:
            if piece.content:
                header = f"<< {piece.provider} >>"
                context_layers.append(f"{header}\n{piece.content}")

        # 6. Renderizar user_message desde el template
        #    Primero resolver placeholders desde el baggage + pieces incluidos
        user_message = self._resolve_placeholders(
            prompt_template, allocated.included, ctx
        )

        # 7. SSOT del contexto de entrada (F2 SPEC-043): schema de cada capa.
        #    Con los flujos del contenido ya presentes (context_layers + system),
        #    se deriva la forma tipada que el agente recibe, sin reemplazar texto.
        context_schemas: dict[str, Any] = {}
        for piece in allocated.included:
            if not piece.included or piece.provider in context_schemas:
                continue
            provider = self._registry.get(piece.provider)
            schema: dict[str, Any] = {}
            if provider is not None:
                try:
                    schema = provider.output_schema() or {}
                except Exception:
                    schema = {}
            if not schema:
                # Fallback SSOT: board engine/context/contracts CONTEXT_SCHEMAS
                # (cub to los 9 providers sin obligar a tocar cada feature).
                try:
                    from .contracts import CONTEXT_SCHEMAS

                    schema = CONTEXT_SCHEMAS.get(piece.provider, {})
                except Exception:
                    schema = {}
            if schema:
                context_schemas[piece.provider] = schema

        return ComposedContext(
            system_message=system_message,
            context_layers=context_layers,
            user_message=user_message,
            provenance=allocated.included + allocated.excluded,
            budget_used=allocated.budget_used,
            budget=allocated.budget,
            context_schemas=context_schemas,
        )

    # ── Métodos internos ───────────────────────────────────────────

    def _collect_pieces(
        self,
        sources: list[dict[str, Any]],
        ctx: Any,
    ) -> list[ContextPiece]:
        """Ejecuta cada provider declarado y recolecta ContextPieces.

        Orden: respeta el orden de declaración en sources.
        Errores: si un provider falla, se salta con log y marca como skipped.

        Cada provider recibe un QueryManager para consultar datos.
        """
        pieces: list[ContextPiece] = []

        # Crear QueryManager desde el chain_dir del ctx
        # Se le pasa task_id/session_id para que las consultas de contexto
        # queden trazadas en el TraceGraph asociadas a la ejecución real
        # (no a "cli" como las queries manuales).
        chain_dir = getattr(ctx, "chain_dir", None)
        query_manager = QueryManager(
            chain_dir,
            task_id=getattr(ctx, "task_id", ""),
            session_id=getattr(ctx, "session_id", ""),
        ) if chain_dir else None

        for source_def in sources:
            provider_name = source_def.get("provider", "")
            config = source_def.get("config", {})

            provider = self._registry.get(provider_name)
            if not provider:
                logger.warning(
                    "ContextComposer: provider '%s' not registered — skipping",
                    provider_name,
                )
                pieces.append(ContextPiece(
                    provider=provider_name,
                    name=f"unregistered provider '{provider_name}'",
                    reason="skipped: provider not registered",
                    included=False,
                ))
                continue

            # Collect instructions (deterministic, no cache needed)
            try:
                instructions = provider.instructions(config, ctx) or ""
            except Exception as e:
                logger.error(
                    "ContextComposer: provider '%s'.instructions() failed: %s",
                    provider_name, e,
                )
                instructions = ""

            # Check cache for collect() results
            task_id = getattr(ctx, "task_id", "")
            chain_dir = getattr(ctx, "chain_dir", "")
            cache_key = (provider_name, _hash_config(config), task_id, chain_dir)
            cached = self._collect_cache.get(cache_key)
            if cached is not None:
                if cached.included or cached.reason in ("skipped", "error"):
                    pieces.append(cached)
                    continue
                # Re-use cached piece (re-attach fresh instructions)
                cached.instructions = instructions
                pieces.append(cached)
                continue

            # Collect data via QueryManager
            try:
                piece = provider.collect(config, ctx, query_manager)
            except Exception as e:
                logger.error(
                    "ContextComposer: provider '%s'.collect() failed: %s",
                    provider_name, e,
                )
                error_piece = ContextPiece(
                    provider=provider_name,
                    name=f"collect() failed: {e}",
                    instructions=instructions,
                    reason="error",
                    included=False,
                )
                self._collect_cache[cache_key] = error_piece
                pieces.append(error_piece)
                continue

            if piece is None:
                # Provider sin datos, solo instructions (si tiene)
                if instructions:
                    pieces.append(ContextPiece(
                        provider=provider_name,
                        name=f"instructions only ({provider_name})",
                        instructions=instructions,
                        reason="instructions_only",
                        included=True,
                        priority=0,
                    ))
                continue

            # Asegurar que instructions esté en el piece
            if instructions and not piece.instructions:
                piece.instructions = instructions
            piece.included = True
            piece.reason = "included"
            # Cache successful result
            self._collect_cache[cache_key] = piece
            pieces.append(piece)

        return pieces

    def invalidate_cache(self, provider_name: str | None = None) -> None:
        """Invalida la caché de collect().

        Args:
            provider_name: si se especifica, invalida solo ese provider.
                          Si es None, invalida toda la caché.
        Útil después de side_effects como update_code_index o build_project_graph.
        """
        if provider_name is None:
            self._collect_cache.clear()
        else:
            keys_to_remove = [k for k in self._collect_cache if k[0] == provider_name]
            for k in keys_to_remove:
                del self._collect_cache[k]

    # ── Placeholder resolution ─────────────────────────────────────

    def _resolve_placeholders(
        self,
        text: str,
        pieces: list[ContextPiece],
        ctx: Any,
    ) -> str:
        """Resuelve {placeholders} en un texto.

        Fuentes de datos (por orden de precedencia):
        1. ctx.baggage (contexto de sesión — incluye extra_data inyectado)
        2. ctx attributes (task_id, chain_dir, etc.)
        3. collect_all(chain_dir) (state, project, task, knowledge — fallback)
        """
        if not text or "{" not in text:
            return text

        # Construir dict desde ctx.baggage + ctx attrs
        ctx_dict: dict[str, Any] = {}
        baggage = getattr(ctx, "baggage", {}) or {}
        if isinstance(baggage, dict):
            ctx_dict.update(baggage)

        for attr in ("task_id", "chain_dir", "session_id"):
            val = getattr(ctx, attr, None)
            if val is not None:
                ctx_dict[attr] = val

        # Fallback: datos de collect_all desde el chain_dir
        chain_dir = getattr(ctx, "chain_dir", None) or ctx_dict.get("chain_dir", "")
        if chain_dir:
            try:
                # La recolección de contexto (collect_all/prompts) es INCUMBENCIA
                # DEL HOST (ADR-WALKER-067): el engine no la recolecta. El host
                # provee el contexto ya resuelto.
                pass
            except Exception:
                pass

        def _replace_var(m: _re.Match) -> str:
            var_name = m.group(1)
            val = ctx_dict.get(var_name, "")
            if isinstance(val, (list, dict)):
                return json.dumps(val, ensure_ascii=False)
            return str(val)

        return _re.sub(r"\{(\w+)\}", _replace_var, text)
