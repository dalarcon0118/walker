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

from ...domain.enums import ExecutionType
from .ports.prompt_manager import IPromptManager
from .loader import TemplateLoader
from .renderer import TemplateRenderer
from .static_loader import canonical_nodes_dir

logger = logging.getLogger(__name__)

# SSOT del valor de ejecución LLM: ExecutionType (domain.enums) — sin duplicar literales.
_EXECUTION_LLM_TASK = ExecutionType.LLM_TASK.value

# Tope del conocimiento inyectable en un prompt (medido: sin tope, un proyecto
# aporta ~18k chars ≈ 4.5k tokens por prompt y el agente no gana precisión).
MAX_KNOWLEDGE_ITEMS = 5
MAX_KNOWLEDGE_CHARS = 1200


class PromptManager(IPromptManager):
    """Implementación del servicio de prompts (loader + renderer + compose)."""

    def __init__(
        self,
        chain_dir: str,
        project_dir: str | None = None,
        ckg_searcher: Any = None,
    ):
        self._chain_dir = chain_dir
        self._loader = TemplateLoader(chain_dir, project_dir)
        self._renderer = TemplateRenderer()
        # Enriquecimiento con conocimiento del proyecto (opt-in). Lo inyecta el
        # composition root: el manager NO importa la feature cognitive_knowledge
        # (R1) — recibe un searcher ya construido.
        self._ckg_searcher = ckg_searcher

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
                        "type": _EXECUTION_LLM_TASK,
                    }
                return {
                    "node_id": node_id,
                    "sub_state": sub_state_key,
                    "template_id": prompt_id or "",
                    "template": "",
                    "data": extra,
                    "type": _EXECUTION_LLM_TASK,
                    "error": f"template '{prompt_id or (node_id + '/' + sub_state_key)}' no encontrado",
                }
            data = self._build_data(extra, None)
            data.update(extra)
            for key, value in self._skills_data(fields).items():
                data.setdefault(key, value)
            return {
                "node_id": node_id,
                "sub_state": sub_state_key,
                "template_id": template_id,
                "template": self._renderer.render(template, data),
                "data": data,
                "type": _EXECUTION_LLM_TASK,
            }

        # Ejecución no-LLM (auto/user_input/gate). Para los user gates con
        # `prompt_id` SÍ se renderiza el briefing: es donde el agente arma la
        # evidencia que el usuario decide, y ahí importan los skills de la fase.
        data = self._build_data(extra, None)
        data.update(extra)
        for key, value in self._skills_data(fields).items():
            data.setdefault(key, value)
        rendered = prompt_literal
        if prompt_id:
            try:
                meta = self._loader.load_with_meta(prompt_id)
                rendered = self._renderer.render(self._template_text(meta), data)
            except Exception as e:  # noqa: BLE001 — briefing no bloquea el gate
                logger.debug("PromptManager: sin template para %s (%s)", prompt_id, e)
                rendered = prompt_literal
        return {
            "node_id": node_id,
            "sub_state": sub_state_key,
            "template_id": prompt_id or "",
            "template": rendered,
            "data": data,
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

    def _build_data(self, context: dict | None, extra_data: dict | None) -> dict:
        data = dict(context or {})
        if extra_data:
            data.update(extra_data)
        # Skills del entorno para la intención de la fase (opt-in por plantilla:
        # el renderer deja vacío el placeholder si nadie lo declara).
        if not any(key.startswith("skills_") for key in data):
            for key, value in self._skills_data(data).items():
                data.setdefault(key, value)
        # Enriquecimiento CKG: solo si el composition root inyectó el searcher.
        # Las claves explícitas del caller SIEMPRE ganan (setdefault).
        for key, value in self._knowledge_data(data).items():
            data.setdefault(key, value)
        return data

    def _skills_data(self, fields: dict) -> dict:
        """Catálogo de skills que matchean la intención declarada por el nodo.

        No inyecta contenido: una línea por skill y la instrucción de cargarla
        con `load_skill`. Sin match, lo dice explícitamente (un silencio se lee
        como "aplicá cualquier cosa").
        """
        if os.environ.get("WALKER_PROMPT_SKILLS") == "0":
            return {}
        intent = (fields or {}).get("skill_intent") or {}
        if not isinstance(intent, dict) or not intent:
            return {}
        from fast_mcp.shared_kernel.skills import format_catalog

        from .collector import collect_skills

        try:
            skills = collect_skills(self._chain_dir, intent)
        except Exception as e:  # noqa: BLE001 — el prompt no depende del FS
            logger.debug("PromptManager: skills fallaron (%s)", e)
            return {}
        catalog = format_catalog(skills)
        if not catalog:
            catalog = (
                "Sin skills locales que matcheen esta fase; resolvé con criterio "
                "propio y decilo en la salida."
            )
        return {"skills_catalog": catalog, "skills_count": str(len(skills))}

    def _knowledge_data(self, data: dict) -> dict:
        """Conocimiento del proyecto como texto listo para templates.

        Claves: `knowledge_global`, `knowledge_relevant`,
        `knowledge_architecture`, `project_architecture`, `project_patterns`,
        `project_quality_gates`.

        Es opt-in a nivel de plantilla: el renderer deja vacío un placeholder
        sin dato, y las plantillas que no usan estas claves no cambian. Así el
        cableado no altera ningún prompt existente (verificado por test).
        """
        if self._ckg_searcher is None or os.environ.get("WALKER_PROMPT_KNOWLEDGE") == "0":
            return {}
        from .collector import collect_knowledge, collect_project_info

        node_id = str(data.get("node_id") or data.get("task_type") or "")
        try:
            knowledge = collect_knowledge(
                self._chain_dir, {"node_id": node_id}, ckg_searcher=self._ckg_searcher
            )
            info = collect_project_info(self._chain_dir, ckg_searcher=self._ckg_searcher)
        except Exception as e:  # noqa: BLE001 — el prompt nunca depende del CKG
            logger.debug("PromptManager: enriquecimiento CKG falló (%s)", e)
            return {}
        return {
            "knowledge_global": _bullets(knowledge.get("global")),
            "knowledge_relevant": _bullets(knowledge.get("relevant")),
            "knowledge_architecture": _bullets(knowledge.get("architecture")),
            "project_architecture": info.get("architecture", ""),
            "project_patterns": _bullets(info.get("patterns")),
            "project_quality_gates": _bullets(info.get("quality_gates")),
        }


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


def _bullets(
    items: Any,
    limit: int = MAX_KNOWLEDGE_ITEMS,
    max_chars: int = MAX_KNOWLEDGE_CHARS,
) -> str:
    """Lista de conocimiento → texto con viñetas para templates.

    Acepta `_source` dicts (CKG) o strings (patterns/quality_gates).

    Se recorta por dos motivos medidos: `knowledge_relevant` de
    react-native-frontend son 9.294 chars (52 docs de tipo implementation) y
    `knowledge_architecture` 6.212. Inyectar eso en cada prompt cuesta ~4.500
    tokens sin que el agente gane precisión; con el tope queda en ~300.
    """
    lines: list[str] = []
    for item in list(items or [])[:limit]:
        text = (
            str(item.get("statement", ""))
            if isinstance(item, dict)
            else str(item)
        ).strip()
        if text:
            lines.append(f"- {text}")
    out = "\n".join(lines)
    if len(out) > max_chars:
        # -1 para que el carácter de elipsis también quepa en el tope.
        out = out[: max_chars - 1].rstrip() + "…"
    return out
