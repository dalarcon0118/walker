"""renderer.py — Renderiza templates con datos. Pura."""
from __future__ import annotations
import json
import re
from typing import Any


class TemplateRenderer:
    def render(self, template: str, data: dict[str, Any]) -> str:
        result = template
        result = re.sub(r"\{\{\s*(\w+)\s*\|\s*tojson\s*\}\}",
                        lambda m: json.dumps(data.get(m.group(1), ""), indent=2, ensure_ascii=False), result)
        result = re.sub(r"\{\{\s*(\w+)\s*\}\}",
                        lambda m: str(data.get(m.group(1), "")), result)
        result = re.sub(r"(?<!\{)\{(\w+)\}(?!\})",
                        lambda m: str(data.get(m.group(1), "")), result)
        return result

    def render_dict(self, template_dict: dict, data: dict[str, Any]) -> dict:
        result = {}
        for k, v in template_dict.items():
            if isinstance(v, str):
                result[k] = self.render(v, data)
            elif isinstance(v, dict):
                result[k] = self.render_dict(v, data)
            elif isinstance(v, list):
                result[k] = [self.render(item, data) if isinstance(item, str) else item for item in v]
            else:
                result[k] = v
        return result
