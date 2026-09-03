"""loader.py — Carga templates de múltiples fuentes (SSOT: static_loader).

Restaurado desde el motor heredado (mcp/features/workflow/application/prompts/
loader.py, eliminado por el commit 63efa79) y adaptado a fast_mcp:
  - `_resolve_project_dir_core` usa ProjectService.resolve_paths en lugar de
    resolve_port(IProjectContext) del motor heredado.
  - `_engine_wf_dir_core` (app.path_resolver) se elimina: el ROOT canónico
    de nodos es templates/nodes/ via static_loader (SSOT), sin necesidad de
    resolución por app/.
  - La excepción `TemplateNotFound` es `TemplateNotFoundError` (fast_mcp).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

from ...domain.errors import TemplateNotFoundError
from .static_loader import canonical_nodes_dir, canonical_templates_dir

logger = logging.getLogger(__name__)


def build_mcp_protocol() -> str:
    """Lee el protocolo MCP canónico (templates/protocol/mcp.md)."""
    from .static_loader import load_static

    return load_static("protocol/mcp")


class TemplateLoader:
    """Carga templates por ID desde múltiples fuentes."""

    def __init__(self, chain_dir: str, project_dir: str | None = None):
        self.chain_dir = chain_dir
        if project_dir:
            self.project_dir = project_dir
        else:
            result = _resolve_project_dir_core(chain_dir)
            if is_successful(result):
                self.project_dir = result.value_or("")
            else:
                logger.debug(
                    "project_dir no resuelto (%s); usando fallback estructural",
                    result.failure(),
                )
                self.project_dir = os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
        self._script_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )

    def load(self, template_id: str) -> str:
        """Carga el template desde la primera fuente legible.

        Las fuentes ilegibles se loggean en debug y se salta a la siguiente —
        la resolución multi-fuente no silencia errores, los hace visibles por
        fuente.
        """
        sources = self._resolve_sources(template_id)
        for path, fmt in sources:
            if path and os.path.exists(path):
                result = _read_source_core(path, fmt)
                if is_successful(result):
                    data = result.value_or(None)
                    if data is None:
                        continue
                    if fmt == "json":
                        if not isinstance(data, dict):
                            continue
                        return data.get("template", data.get("prompt", ""))
                    return data
                logger.debug(
                    "fuente %s ilegible (%s); probando siguiente",
                    path, result.failure(),
                )
        raise TemplateNotFoundError(template_id)

    def load_with_meta(self, template_id: str) -> dict[str, Any]:
        sources = self._resolve_sources(template_id)
        for path, fmt in sources:
            if path and os.path.exists(path):
                result = _read_source_core(path, fmt)
                if is_successful(result):
                    data = result.value_or(None)
                    if data is None:
                        continue
                    if fmt == "json":
                        return data
                    return {"template": data}
                logger.debug(
                    "fuente %s ilegible (%s); probando siguiente",
                    path, result.failure(),
                )
        raise TemplateNotFoundError(template_id)

    def _node_prompt_sources(self, wf: str, template_id: str) -> list[tuple[str | None, str]]:
        """Fuentes para prompts de nodo (formato .prompt.json) dentro de un dir."""
        if not wf or not os.path.isdir(wf):
            return []
        if "/" in template_id:
            node, _, prompt_name = template_id.partition("/")
            return [(os.path.join(wf, node, f"{prompt_name}.prompt.json"), "json")]
        return [
            (os.path.join(wf, node, f"{template_id}.prompt.json"), "json")
            for node in sorted(os.listdir(wf))
        ]

    def _resolve_sources(self, template_id: str) -> list[tuple[str | None, str]]:
        canonical_dir = canonical_templates_dir()
        nodes_dir = canonical_nodes_dir()
        tpl_dir = os.path.join(self.project_dir, "templates")
        sources: list[tuple[str | None, str]] = []
        # ROOT CANÓNICO (SSOT) primero.
        sources.append((os.path.join(canonical_dir, template_id), "text"))
        sources.append((os.path.join(canonical_dir, f"{template_id}.md"), "text"))
        sources.append((os.path.join(canonical_dir, f"{template_id}.txt"), "text"))
        # Prompts de nodo: templates/nodes/ (motor SSOT) > proyecto-local.
        sources.extend(self._node_prompt_sources(nodes_dir, template_id))
        sources.append((os.path.join(tpl_dir, template_id), "text"))
        sources.append((os.path.join(tpl_dir, f"{template_id}.txt"), "text"))
        sources.append((os.path.join(tpl_dir, f"{template_id}.md"), "text"))
        return sources


# ── Cores @safe (Result pattern) ─────────────────

@safe
def _read_source_core(path: str, fmt: str) -> dict | str:
    """Core puro: lee una fuente de template (puede lanzar)."""
    if fmt == "json":
        with open(path) as f:
            return json.load(f)
    with open(path) as f:
        return f.read()


@safe
def _resolve_project_dir_core(chain_dir: str) -> str:
    """Core puro de TemplateLoader.__init__ (puede lanzar).

    Resuelve project_root vía ProjectService.resolve_paths (fast_mcp).
    """
    from fast_mcp.shared_kernel.project_support import ProjectService

    paths = ProjectService.resolve_paths(chain_dir)
    return getattr(paths, "project_root", "")
