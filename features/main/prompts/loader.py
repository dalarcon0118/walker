"""loader.py — Carga templates de múltiples fuentes."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

from features.project import ProjectService

logger = logging.getLogger(__name__)


class TemplateNotFound(Exception):
    pass


def canonical_templates_dir() -> str:
    """ROOT ÚNICO de templates: features/prompts/templates/ (organizado por subpaths).

    SSOT de todos los prompts servidos por features/prompts:
      - nodes/<node>/<prompt>.prompt.json  → prompts del workflow del motor
      - protocol/*.md                      → protocolos (MCP / REST)
      - gates/*.md                         → gate briefings
    No depende de proyecto ni de chain_dir.
    """
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def canonical_nodes_dir() -> str:
    """Subpath de prompts de nodo del motor: features/prompts/templates/nodes/."""
    return os.path.join(canonical_templates_dir(), "nodes")


def load_static(template_id: str) -> str:
    """Carga un template SOLO desde el root canónico (features/prompts/templates).

    Para prompts globales del servidor (protocolos, briefing) que no dependen
    de un proyecto/chain_dir. Fallback de última instancia: loader completo.
    """
    root = canonical_templates_dir()
    candidates = (
        os.path.join(root, template_id),
        os.path.join(root, f"{template_id}.md"),
        os.path.join(root, f"{template_id}.txt"),
    )
    for path in candidates:
        if os.path.exists(path):
            result = _read_text_core(path)
            if is_successful(result):
                return result.value_or("")
            logger.debug(
                "template %s ilegible (%s); probando siguiente fuente",
                path, result.failure(),
            )
    raise TemplateNotFound(f"Template '{template_id}' no encontrado en {root}")


def build_mcp_protocol() -> str:
    from .protocol_builder import build_mcp_protocol as _build
    return _build()


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
                self.project_dir = os.path.dirname(os.path.dirname(chain_dir))
        self._script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def load(self, template_id: str) -> str:
        """Carga el template desde la primera fuente legible.

        Result pattern (convention-001): las fuentes ilegibles se loggean en
        debug y se salta a la siguiente — la resolucion multi-fuente no
        silencia errores, los hace visibles por fuente.
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
                        # JSON valido pero no objeto (lista/int): fuente invalida.
                        # En el original este caso saltaba de fuente via except;
                        # aqui la guarda explicita preserva esa semantica.
                        if not isinstance(data, dict):
                            continue
                        return data.get("template", data.get("prompt", ""))
                    return data
                logger.debug(
                    "fuente %s ilegible (%s); probando siguiente",
                    path, result.failure(),
                )
        raise TemplateNotFound(f"Template '{template_id}' no encontrado en ninguna fuente")

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
        raise TemplateNotFound(f"Template '{template_id}' no encontrado")

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
        prompt_dir = os.path.join(self._script_dir, "prompt", "templates")
        # ELIMINADO (ADR-WALKER-061): fuente legacy project_dir/workflow.v3/prompts.
        nodes_dir = canonical_nodes_dir()  # SSOT del motor: templates/nodes/
        # Fallback legacy: prompts del workflow v3.1 que aún viven en la data del motor
        result = _engine_wf_dir_core()
        if is_successful(result):
            engine_wf_dir = result.value_or("")
        else:
            logger.debug("engine workflow dir no resuelto (%s)", result.failure())
            engine_wf_dir = ""
        tpl_dir = os.path.join(self.project_dir, "templates")
        legacy_tpl = os.path.join(self._script_dir, "templates")
        internal_tpl = os.path.join(self._script_dir, "_internal", "templates")
        prompts_dir = os.path.join(os.path.dirname(self.project_dir), "prompts")
        sources = []
        # ROOT CANÓNICO (SSOT) primero: features/prompts/templates/<subpath>/
        canonical_dir = canonical_templates_dir()
        sources.append((os.path.join(canonical_dir, template_id), "text"))
        sources.append((os.path.join(canonical_dir, f"{template_id}.md"), "text"))
        sources.append((os.path.join(canonical_dir, f"{template_id}.txt"), "text"))
        # Fuentes legacy/adicionales como fallback (backward compatibility)
        sources.append((os.path.join(prompt_dir, template_id), "text"))
        sources.append((os.path.join(prompt_dir, f"{template_id}.md"), "text"))
        sources.append((os.path.join(prompt_dir, f"{template_id}.txt"), "text"))
        # Precedencia de prompts de nodo: proyecto-local > templates/nodes/ (motor) > data del motor
        for wf in (nodes_dir, engine_wf_dir):
            sources.extend(self._node_prompt_sources(wf, template_id))
        sources.append((os.path.join(tpl_dir, template_id), "text"))
        sources.append((os.path.join(tpl_dir, f"{template_id}.txt"), "text"))
        sources.append((os.path.join(tpl_dir, f"{template_id}.md"), "text"))
        sources.append((os.path.join(legacy_tpl, template_id), "text"))
        sources.append((os.path.join(legacy_tpl, f"{template_id}.txt"), "text"))
        sources.append((os.path.join(internal_tpl, template_id), "text"))
        sources.append((os.path.join(internal_tpl, f"{template_id}.txt"), "text"))
        sources.append((os.path.join(prompts_dir, f"{template_id}.md"), "text"))
        return sources


# ── Cores @safe (Result pattern, convention-001) ─────────────────

@safe
def _read_text_core(path: str) -> str:
    """Core puro: lee un archivo de texto (puede lanzar)."""
    with open(path) as f:
        return f.read()


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
    """Core puro de TemplateLoader.__init__ (puede lanzar)."""
    paths = ProjectService.resolve_paths(chain_dir)
    return paths.project_root


@safe
def _engine_wf_dir_core() -> str:
    """Core puro: dir de prompts del engine workflow (puede lanzar)."""
    from shared.core.path_resolver import get_engine_workflow_dir

    return str(get_engine_workflow_dir() / "prompts")
