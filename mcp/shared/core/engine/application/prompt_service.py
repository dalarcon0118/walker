"""application/prompt_service.py — PromptService (SRP, ADR-WALKER-067 F3/C).

Responsabilidad: resolución de prompts del flujo. El engine NO recolecta
contexto de negocio para prompts (ADR-WALKER-067 §2.3: motor anémico); el
host lo inyecta externamente vía extra_data/context del caller.
"""
from __future__ import annotations

from typing import Any


class _ComposerContext:
    """ExecutionContext liviano para el ContextComposer.

    Existe para evitar acoplar el composer al ExecutionContext real,
    que tiene muchas más dependencias. Fase 1: mínimo necesario.
    """

    def __init__(
        self, chain_dir: str = "", task_id: str = "", baggage: dict | None = None
    ):
        self.chain_dir = chain_dir
        self.task_id = task_id
        self.baggage = baggage or {}


class PromptService:
    """Resolución de prompts del flujo (incumbencia del host).

    SRP: única responsabilidad — resolver prompt_id / status prompt / node
    briefing para el LLM. No conoce repos, registries ni negocio.
    """

    def resolve_prompt(
        self,
        prompt_id: str,
        chain_dir: str,
        task_id: str = "",
        context_config: dict | None = None,
        execution_ctx: Any | None = None,
        extra_data: dict | None = None,
    ) -> str:
        """Resuelve prompt_id usando PromptManager (módulo centralizado).

        Si context_config está presente, usa compose() (provider-based).
        Si no, usa resolve() (template-based).

        Args:
            extra_data: datos adicionales inyectados sobre collect_all() (prioridad alta)
        """
        try:
            # El contexto de prompts es INCUMBENCIA DEL HOST (ADR-WALKER-067):
            # el engine no recolecta datos de negocio para prompts. El host los
            # provee externamente (vía extra_data/context del caller).
            pm = {}
            ctx = {"task_id": task_id, "node_id": prompt_id.split("/")[-1]}
            if execution_ctx:
                ctx.update(getattr(execution_ctx, "baggage", {}) or {})

            if context_config:
                # FIX (prompt rendering): inyectar extra_data (sprint_ctx con
                # Q&A, task info, etc.) en el baggage del composer para que los
                # placeholders {q1}, {a1}, {task_title}... se resuelvan con
                # datos reales. Antes el extra_data solo se usaba en resolve()
                # (sin context.sources), no en compose().
                baggage = dict(getattr(execution_ctx, "baggage", {}) or {})
                if extra_data:
                    baggage.update(extra_data)
                composer_ctx = _ComposerContext(
                    chain_dir=chain_dir,
                    task_id=task_id or "",
                    baggage=baggage,
                )
                return pm.compose(
                    node_config=context_config,
                    prompt_template=prompt_id,
                    ctx=composer_ctx,
                    chain_dir=chain_dir,
                    extra_data=extra_data,
                )

            return pm.resolve(prompt_id, context=ctx, extra_data=extra_data)
        except Exception as e:
            return f"[error resolving prompt '{prompt_id}': {e}]"

    def resolve_status_prompt(self, sub_state, ctx) -> str:
        """Resuelve el prompt para status: usa prompt_id si existe, si no on_entry_message.

        Inyecta sprint context como extra_data para que templates {variables}
        se resuelvan con datos reales del STATE layer.
        Si el sub-estado declara context.sources, usa compose() para incluir
        las capas de contexto (cpg, business, ckg, etc.) en el prompt.
        """
        if sub_state.prompt_id:
            # El contexto de placeholders (Q&A, task info, delta) es del HOST;
            # el engine no lo builda (PromptContextBuilder sale del motor).
            sprint_ctx = {}
            context_config = sub_state.context if sub_state.context else None
            resolved = self.resolve_prompt(
                sub_state.prompt_id,
                ctx.chain_dir,
                ctx.task_id,
                context_config=context_config,
                execution_ctx=ctx,
                extra_data=sprint_ctx,
            )
            if resolved and not resolved.startswith("[error"):
                return resolved
        return sub_state.on_entry_message or ""

    @staticmethod
    def build_node_briefing(
        node: Any, sub_state: Any
    ) -> str:
        """Construye el briefing del nodo actual para el LLM.

        Es metadata del flujo de ejecución — no es un provider de datos.
        Se inyecta como sección fija en el mensaje antes del contexto.
        """
        lines = ["=== NODE BRIEFING ==="]
        if node:
            label = node.label or node.node_id
            lines.append(f"Node: {node.node_id} ({label})")
            if node.description:
                lines.append(f"Purpose: {node.description}")
        if sub_state:
            ss_label = sub_state.label or sub_state.key
            lines.append(f"Current step: {sub_state.key} — {ss_label}")
            if sub_state.on_success:
                lines.append(f'→ On success: proceed to "{sub_state.on_success}"')
            if sub_state.on_failure:
                lines.append(f'→ On failure: revert to "{sub_state.on_failure}"')
            if sub_state.side_effects:
                se_list = [se.type for se in sub_state.side_effects]
                lines.append(f"Side effects: {', '.join(se_list)}")
            else:
                lines.append("Side effects: (none)")
            # ── GATE: required_tools (auto-consulta obligatoria) ────────
            if sub_state.required_tools:
                lines.append("")
                lines.append(
                    "REQUIRED TOOLS — must invoke BEFORE submitting this step:"
                )
                for t in sub_state.required_tools:
                    tool_id = t.get("id", "?")
                    hint = t.get("command_hint", "")
                    lines.append(f"  • {tool_id}: {hint}")
                lines.append(
                    "  (The engine will BLOCK the submit if no kg_query for "
                    "these layers is found in the trace for this task.)"
                )
        return "\n".join(lines)