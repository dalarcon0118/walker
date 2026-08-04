"""manager.py — PromptManager: único punto de entrada para prompts."""
from __future__ import annotations
import os
import json
from datetime import datetime, timezone
from typing import Any
from .loader import TemplateLoader
from .renderer import TemplateRenderer
from .collector import collect_all
from features.engine.infrastructure.config import get as cfg


def _debug_enabled(level: int = 1) -> bool:
    try:
        config_file = cfg("config_file", "")
        if config_file and os.path.exists(config_file):
            with open(config_file) as f:
                return json.load(f).get("debug_level", 0) >= level
    except Exception:
        pass
    return False


def _log_prompt(prompt_id: str, prompt_text: str, metadata: dict) -> None:
    if not _debug_enabled(1):
        return
    try:
        chain_dir = metadata.get("chain_dir", "")
        prompts_dir = os.path.join(os.path.dirname(chain_dir), "data", "prompts")
        os.makedirs(prompts_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        safe_id = prompt_id.replace("/", "_")
        path = os.path.join(prompts_dir, f"{ts}_{safe_id}.json")
        with open(path, "w") as f:
            json.dump({"prompt_id": prompt_id, "timestamp": datetime.now(timezone.utc).isoformat(),
                       "metadata": metadata, "prompt": prompt_text}, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


class PromptManager:
    def __init__(self, chain_dir: str, project_dir: str | None = None):
        self.chain_dir = chain_dir
        self.project_dir = project_dir
        self._loader = TemplateLoader(chain_dir, project_dir)
        self._renderer = TemplateRenderer()

    def resolve(self, template_id: str, context: dict | None = None, extra_data: dict | None = None) -> str:
        try:
            template = self._loader.load(template_id)
        except Exception as e:
            return f"[error loading template '{template_id}': {e}]"
        ctx = context or {}
        data = collect_all(chain_dir=self.chain_dir, node_id=ctx.get("node_id"), context=ctx)
        if extra_data:
            data.update(extra_data)
        result = self._renderer.render(template, data)
        _log_prompt(template_id, result, {"chain_dir": self.chain_dir, **(context or {})})
        return result

    def render_template(self, template_name: str, data: dict[str, Any]) -> str:
        try:
            template = self._loader.load(template_name)
        except Exception as e:
            return f"[error loading template '{template_name}': {e}]"
        result = self._renderer.render(template, data)
        _log_prompt(template_name, result, {"chain_dir": self.chain_dir})
        return result

    def resolve_with_meta(self, template_id: str, context: dict | None = None, extra_data: dict | None = None) -> dict[str, Any]:
        try:
            meta = self._loader.load_with_meta(template_id)
        except Exception as e:
            return {"error": str(e)}
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
                rel = os.path.relpath(os.path.join(dirpath, f), root)
                result.append(rel.replace(os.sep, "/"))
        return sorted(result)

    def compose(self, node_config: dict, prompt_template: str, ctx: Any, chain_dir: str | None = None, extra_data: dict | None = None) -> str:
        from features.engine.context.composer import ContextComposer
        from features.engine.bootstrap import build_context_registry
        cg_dir = chain_dir or getattr(ctx, "chain_dir", self.chain_dir)
        # FIX (prompt rendering): si prompt_template es un ID de template
        # (ej. "analyze/review" sin placeholders), cargar el template REAL.
        # Antes se pasaba el ID literal como user_message y el informe del
        # gate nunca se renderizaba (el usuario no veía el contenido).
        if "{" not in prompt_template:
            try:
                template_text = self._loader.load(prompt_template)
                if template_text and not template_text.startswith("[error"):
                    prompt_template = template_text
            except Exception:
                pass
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
