"""manager.py — PromptManager: orquesta loader + renderer (SSOT devuelto).

Restaurado desde el motor heredado (mcp/features/workflow/application/prompts/
manager.py, eliminado por el commit 63efa79) y adaptado a fast_mcp:

El original dependía de `ContextComposer` + `build_context_registry` del motor
heredado (shared/core/engine), que no existe en fast_mcp. Esta versión
reconstruye el contrato del port IPromptManager de forma autónoma:

  - `resolve` / `resolve_with_meta`: TemplateLoader + TemplateRenderer.
  - `resolve_for_sub_state`: carga dinámica por tipo de nodo → un sub-estado
    con `execution == "llm_task"` resuelve templates/nodes/<nodo>/<sub>.
    Si el sub-estado declara `prompt`/`prompt_id`, se usa ése primero.
  - `compose`: construye el usuario-mensaje con los datos del ctx (sin
    ContextComposer), renderiza y devuelve mensaje agente.

Registrable por clave lógica (service locator del host) y consumible vía
IPromptManager (nunca por import directo feature->feature).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from .ports.prompt_manager import IPromptManager
from .loader import TemplateLoader
from .renderer import TemplateRenderer
from .static_loader import canonical_nodes_dir

logger = logging.getLogger(__name__)

_EXECUTION_LLM_TASK = "llm_task"


class PromptManager(IPromptManager):
    """Implementación del servicio de prompts (loader + renderer + compose)."""

    def __init__(self, chain_dir: str, project_dir: str | None = None):
        self._chain_dir = chain_dir
        self._loader = TemplateLoader(chain_dir, project_dir)
        self._renderer = TemplateRenderer()

    # ── IPromptManager ────────────────────────────

    def resolve(
        self,
        template_id: str,
        context: dict | None = None,
        extra_data: dict | None = None,
    ) -> str:
        template = self._loader.load(template_id)
        data = self._build_data(context, extra_data)
        return self._renderer.render(template, data)

    def resolve_with_meta(
        self,
        template_id: str,
        context: dict | None = None,
        extra_data: dict | None = None,
    ) -> dict[str, Any]:
        meta = self._loader.load_with_meta(template_id)
        template = self._template_text(meta)
        data = self._build_data(context, extra_data)
        return {
            "template_id": template_id,
            "template": self._renderer.render(template, data),
            "data": data,
            "meta": meta,
            "type": meta.get("type", meta.get("meta", {}).get("type", "text")),
        }

    def resolve_for_sub_state(
        self,
        node_id: str,
        sub_state_key: str,
        execution: str,
        fields: dict | None = None,
        extra_data: dict | None = None,
    ) -> dict[str, Any]:
        """Resuelve el prompt del sub-estado actual.

        Orden de precedencia para prompts LLM:
          1. `prompt_id` explícito del sub-estado.
          2. `prompt` literal del sub-estado.
          3. Template por convención: nodes/<node_id>/<sub_state_key>.prompt.json

        Para ejecución no-LLM devuelve un marcador (no hay conserta LLM).
        """
        fields = fields or {}
        exec_norm = (execution or "").lower()
        prompt_id = fields.get("prompt_id") or ""
        prompt_literal = fields.get("prompt") or ""
        extra = {**(extra_data or {})}
        extra.update(
            {
                "node_id": node_id,
                "sub_state": sub_state_key,
                "execution": exec_norm,
            }
        )

        if exec_norm == _EXECUTION_LLM_TASK:
            template_id = prompt_id or f"{node_id}/{sub_state_key}"
            try:
                meta = self._loader.load_with_meta(template_id)
                template = self._template_text(meta)
            except Exception as e:
                logger.warning(
                    "Sin template para llm_task %s/%s (%s); usando prompt literal o vacío",
                    node_id, sub_state_key, e,
                )
                if prompt_literal:
                    return {
                        "node_id": node_id,
                        "sub_state": sub_state_key,
                        "template_id": prompt_id or f"{node_id}/{sub_state_key}",
                        "template": self._renderer.render(prompt_literal, extra),
                        "data": extra,
                        "type": "llm_task",
                    }
                return {
                    "node_id": node_id,
                    "sub_state": sub_state_key,
                    "template_id": prompt_id or "",
                    "template": "",
                    "data": extra,
                    "type": "llm_task",
                    "error": f"template '{prompt_id or (node_id + '/' + sub_state_key)}' no encontrado",
                }
            data = self._build_data(extra, None)
            data.update(extra)
            return {
                "node_id": node_id,
                "sub_state": sub_state_key,
                "template_id": template_id,
                "template": self._renderer.render(template, data),
                "data": data,
                "type": "llm_task",
            }

        # Ejecución no-LLM: sin conserta (auto/user_input/gate). Marcador.
        return {
            "node_id": node_id,
            "sub_state": sub_state_key,
            "template_id": prompt_id or "",
            "template": prompt_literal,
            "data": extra,
            "type": exec_norm or "unknown",
        }

    def compose(
        self,
        node_config: dict,
        prompt_template: str,
        ctx: Any,
        chain_dir: str | None = None,
        extra_data: dict | None = None,
    ) -> str:
        """Compone el usuario-mensaje a partir de los datos del ctx (sin
        ContextComposer: dependencia del motor heredado)."""
        node_id = node_config.get("id") or (node_config.get("node_id") or "")
        sub_key = node_config.get("sub_state") or node_config.get("sub_state_key") or ""
        data = {}
        if ctx is not None:
            data = _ctx_to_dict(ctx)
        data["node_id"] = node_id
        data["sub_state"] = sub_key
        if extra_data:
            data.update(extra_data)
        return self._renderer.render(prompt_template, data)

    def available_templates(self) -> list[str]:
        nodes_dir = canonical_nodes_dir()
        out: list[str] = []
        if os.path.isdir(nodes_dir):
            for node in sorted(os.listdir(nodes_dir)):
                node_path = os.path.join(nodes_dir, node)
                if os.path.isdir(node_path):
                    for name in sorted(os.listdir(node_path)):
                        if name.endswith(".prompt.json"):
                            out.append(f"{node}/{name[: -len('.prompt.json')]}")
        return out

    # ── helpers ───────────────────────────────────

    @staticmethod
    def _template_text(meta: dict) -> str:
        if isinstance(meta, dict):
            if "template" in meta:
                return meta["template"]
            if "prompt" in meta:
                return meta["prompt"]
        return str(meta)

    @staticmethod
    def _build_data(context: dict | None, extra_data: dict | None) -> dict:
        data = dict(context or {})
        if extra_data:
            data.update(extra_data)
        return data


def _ctx_to_dict(ctx: Any) -> dict:
    """Serializa un ctx arbitrario (WorkflowContext, dict, dataclass) a dict."""
    if isinstance(ctx, dict):
        return dict(ctx)
    if isinstance(ctx, (str, int, float, bool)) or ctx is None:
        return {}
    if hasattr(ctx, "__dataclass_fields__"):
        from dataclasses import fields

        return {
            f.name: getattr(ctx, f.name)
            for f in fields(ctx)
            if not f.name.startswith("_")
        }
    if hasattr(ctx, "__dict__"):
        return dict(ctx.__dict__)
    return {}
