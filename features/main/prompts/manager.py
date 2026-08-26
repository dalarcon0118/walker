"""manager.py — PromptManager: único punto de entrada para prompts."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

from .loader import TemplateLoader
from .renderer import TemplateRenderer
from .collector import collect_all
from features.main.prompts.application.ports.prompt_manager import IPromptManager
from shared.core.engine.infrastructure.config import get as cfg

logger = logging.getLogger(__name__)


def _debug_enabled(level: int = 1) -> bool:
    """Best-effort: debug habilitado segun el config_file del motor.

    Result pattern: el core @safe nunca lanza; ante config ilegible se
    degrada a debug desactivado con log debug (convention-001).
    """
    result = _debug_level_core()
    if is_successful(result):
        return result.value_or(0) >= level
    logger.debug("config no legible; debug desactivado (%s)", result.failure())
    return False


def _log_prompt(prompt_id: str, prompt_text: str, metadata: dict) -> None:
    """Persiste el prompt en data/prompts (best-effort observabilidad)."""
    if not _debug_enabled(1):
        return
    result = _log_prompt_core(prompt_id, prompt_text, metadata)
    if not is_successful(result):
        logger.debug("log de prompt '%s' fallo (%s)", prompt_id, result.failure())


class PromptManager(IPromptManager):
    """Implementación del puerto IPromptManager (features/prompts).

    Vedado: chain_dir + templates SSOT canónicos. Cada capa (engine, CLI)
    lo resuelve por clave DI ("prompt_manager") o lo construye directo.
    """

    def __init__(self, chain_dir: str, project_dir: str | None = None):
        self.chain_dir = chain_dir
        self.project_dir = project_dir
        self._loader = TemplateLoader(chain_dir, project_dir)
        self._renderer = TemplateRenderer()

    def resolve(self, template_id: str, context: dict | None = None, extra_data: dict | None = None) -> str:
        result = _load_template_core(self._loader, template_id)
        if is_successful(result):
            template = result.value_or("")
        else:
            return f"[error loading template '{template_id}': {result.failure()}]"
        ctx = context or {}
        data = collect_all(chain_dir=self.chain_dir, node_id=ctx.get("node_id"), context=ctx)
        if extra_data:
            data.update(extra_data)
        result = self._renderer.render(template, data)
        _log_prompt(template_id, result, {"chain_dir": self.chain_dir, **(context or {})})
        return result

    def render_template(self, template_name: str, data: dict[str, Any]) -> str:
        result = _load_template_core(self._loader, template_name)
        if is_successful(result):
            template = result.value_or("")
        else:
            return f"[error loading template '{template_name}': {result.failure()}]"
        result = self._renderer.render(template, data)
        _log_prompt(template_name, result, {"chain_dir": self.chain_dir})
        return result

    def resolve_with_meta(self, template_id: str, context: dict | None = None, extra_data: dict | None = None) -> dict[str, Any]:
        result = _load_template_meta_core(self._loader, template_id)
        if is_successful(result):
            meta = result.value_or({})
        else:
            return {"error": str(result.failure())}
        template = meta.get("template", meta.get("prompt", ""))
        if not template:
            return {"error": f"template '{template_id}' no tiene campo template"}
        ctx = context or {}
        data = collect_all(chain_dir=self.chain_dir, node_id=ctx.get("node_id"), context=ctx)
        if extra_data:
            data.update(extra_data)
        rendered = self._renderer.render(template, data)
        result = {"template": rendered, "data": data}
        if "input_schema" in meta:
            result["input_schema"] = meta["input_schema"]
        if "usage" in meta:
            result["usage"] = meta["usage"]
        return result

    def available_templates(self) -> list[str]:
        """Lista los templates del ROOT CANÓNICO features/prompts/templates/.

        Recorre subpaths (gates/, protocol/, ...) y devuelve rutas relativas
        como IDs (ej. 'protocol/mcp.md', 'gates/gate-briefing.md').
        """
        from .loader import canonical_templates_dir
        root = canonical_templates_dir()
        if not os.path.isdir(root):
            return []
        result = []
        for dirpath, _dirs, files in os.walk(root):
            for f in sorted(files):
                # Artefactos de OS (Finder/carpetas) no son templates:
                # un .DS_Store en templates/ no debe alterar el catálogo SSOT.
                if f == ".DS_Store" or f.startswith("."):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, f), root)
                result.append(rel.replace(os.sep, "/"))
        return sorted(result)

    def compose(self, node_config: dict, prompt_template: str, ctx: Any, chain_dir: str | None = None, extra_data: dict | None = None) -> str:
        from shared.core.engine.context.composer import ContextComposer
        from shared.core.engine.bootstrap import build_context_registry
        cg_dir = chain_dir or getattr(ctx, "chain_dir", self.chain_dir)
        # FIX (prompt rendering): si prompt_template es un ID de template
        # (ej. "analyze/review" sin placeholders), cargar el template REAL.
        # Antes se pasaba el ID literal como user_message y el informe del
        # gate nunca se renderizaba (el usuario no veía el contenido).
        if "{" not in prompt_template:
            result = _load_template_core(self._loader, prompt_template)
            if is_successful(result):
                template_text = result.value_or("")
                if template_text and not template_text.startswith("[error"):
                    prompt_template = template_text
            else:
                logger.debug(
                    "template '%s' no cargable; se usa el ID literal (%s)",
                    prompt_template, result.failure(),
                )
        # Registry completo: utilidades del engine + providers de negocio
        # de cada feature (cpg, ckg, business, state, delta, trace, project).
        # El engine NO conoce las capas; el bootstrap las registra externamente.
        composer = ContextComposer(build_context_registry())
        composed = composer.compose(node_config=node_config, prompt_template=prompt_template, ctx=ctx)
        parts = []
        if composed.system_message:
            parts.append(composed.system_message)
        if composed.context_layers:
            parts.append("\n\n".join(composed.context_layers))
        if composed.user_message:
            parts.append(composed.user_message)
        message = "\n\n".join(parts)
        data = collect_all(chain_dir=cg_dir, context={"task_id": getattr(ctx, "task_id", "")})
        if extra_data:
            data.update(extra_data)
        result = self._renderer.render(message, data)
        _log_prompt("compose", result, {"chain_dir": cg_dir, "task_id": getattr(ctx, "task_id", "")})
        return result


# ── Cores @safe (Result pattern, convention-001) ─────────────────

@safe
def _debug_level_core() -> int:
    """Core puro de _debug_enabled (puede lanzar)."""
    config_file = cfg("config_file", "")
    if config_file and os.path.exists(config_file):
        with open(config_file) as f:
            return json.load(f).get("debug_level", 0)
    return 0


@safe
def _log_prompt_core(prompt_id: str, prompt_text: str, metadata: dict) -> None:
    """Core puro: persiste el prompt de debug (best-effort, puede lanzar)."""
    chain_dir = metadata.get("chain_dir", "")
    prompts_dir = os.path.join(os.path.dirname(chain_dir), "data", "prompts")
    os.makedirs(prompts_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    safe_id = prompt_id.replace("/", "_")
    path = os.path.join(prompts_dir, f"{ts}_{safe_id}.json")
    with open(path, "w") as f:
        json.dump(
            {
                "prompt_id": prompt_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "metadata": metadata,
                "prompt": prompt_text,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )


@safe
def _load_template_core(loader: TemplateLoader, template_id: str) -> str:
    """Core puro: carga un template via TemplateLoader (puede lanzar)."""
    return loader.load(template_id)


@safe
def _load_template_meta_core(loader: TemplateLoader, template_id: str) -> dict[str, Any]:
    """Core puro: carga template + meta (puede lanzar)."""
    return loader.load_with_meta(template_id)
