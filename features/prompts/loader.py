"""loader.py — Carga templates de múltiples fuentes."""
from __future__ import annotations
import json
import os
from typing import Any
from features.project import ProjectService


class TemplateNotFound(Exception):
    pass


class TemplateLoader:
    """Carga templates por ID desde múltiples fuentes."""
    def __init__(self, chain_dir: str, project_dir: str | None = None):
        self.chain_dir = chain_dir
        if project_dir:
            self.project_dir = project_dir
        else:
            try:
                paths = ProjectService.resolve_paths(chain_dir)
                self.project_dir = paths.project_root
            except Exception:
                self.project_dir = os.path.dirname(os.path.dirname(chain_dir))
        self._script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def load(self, template_id: str) -> str:
        sources = self._resolve_sources(template_id)
        for path, fmt in sources:
            if path and os.path.exists(path):
                try:
                    if fmt == "json":
                        data = json.load(open(path))
                        return data.get("template", data.get("prompt", ""))
                    return open(path).read()
                except Exception:
                    continue
        raise TemplateNotFound(f"Template '{template_id}' no encontrado en ninguna fuente")

    def load_with_meta(self, template_id: str) -> dict[str, Any]:
        sources = self._resolve_sources(template_id)
        for path, fmt in sources:
            if path and os.path.exists(path):
                try:
                    if fmt == "json":
                        return json.load(open(path))
                    return {"template": open(path).read()}
                except Exception:
                    continue
        raise TemplateNotFound(f"Template '{template_id}' no encontrado")

    def _resolve_sources(self, template_id: str) -> list[tuple[str | None, str]]:
        prompt_dir = os.path.join(self._script_dir, "prompt", "templates")
        wf_dir = os.path.join(self.project_dir, "workflow.v3", "prompts")
        # Plantilla canónica del motor (SSOT) — prompts del workflow v3.1
        try:
            from libs.path_resolver import get_engine_workflow_dir
            engine_wf_dir = str(get_engine_workflow_dir() / "prompts")
        except Exception:
            engine_wf_dir = ""
        tm_dir = os.path.join(self.project_dir, "task-manifests")
        tpl_dir = os.path.join(self.project_dir, "templates")
        legacy_tpl = os.path.join(self._script_dir, "templates")
        internal_tpl = os.path.join(self._script_dir, "_internal", "templates")
        prompts_dir = os.path.join(os.path.dirname(self.project_dir), "prompts")
        sources = []
        sources.append((os.path.join(prompt_dir, template_id), "text"))
        sources.append((os.path.join(prompt_dir, f"{template_id}.md"), "text"))
        sources.append((os.path.join(prompt_dir, f"{template_id}.txt"), "text"))
        for wf in (wf_dir, engine_wf_dir):
            if os.path.isdir(wf):
                if "/" in template_id:
                    node, _, prompt_name = template_id.partition("/")
                    sources.append((os.path.join(wf, node, f"{prompt_name}.prompt.json"), "json"))
                else:
                    for node in sorted(os.listdir(wf)):
                        sources.append((os.path.join(wf, node, f"{template_id}.prompt.json"), "json"))
        sources.append((os.path.join(tm_dir, f"{template_id}.json"), "json"))
        sources.append((os.path.join(tpl_dir, template_id), "text"))
        sources.append((os.path.join(tpl_dir, f"{template_id}.txt"), "text"))
        sources.append((os.path.join(tpl_dir, f"{template_id}.md"), "text"))
        sources.append((os.path.join(legacy_tpl, template_id), "text"))
        sources.append((os.path.join(legacy_tpl, f"{template_id}.txt"), "text"))
        sources.append((os.path.join(internal_tpl, template_id), "text"))
        sources.append((os.path.join(internal_tpl, f"{template_id}.txt"), "text"))
        sources.append((os.path.join(prompts_dir, f"{template_id}.md"), "text"))
        return sources
