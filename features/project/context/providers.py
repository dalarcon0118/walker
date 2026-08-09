"""project/context/providers.py — Providers de contexto del proyecto."""
from __future__ import annotations
import json
import os
from typing import Any

from libs.context import ContextPiece, ContextProvider, fmt_tokens


def _derive_project_id(chain_dir: str) -> str | None:
    """Deriva el project_id desde chain_dir.

    chain_dir típico: .cognitive/projects/<project_id>/delta/chain-xxx/
    """
    parent = (
        os.path.dirname(os.path.dirname(os.path.dirname(chain_dir)))
        if chain_dir
        else ""
    )
    if parent and parent.endswith("/projects"):
        return os.path.basename(os.path.dirname(os.path.dirname(chain_dir)))
    parts = chain_dir.split(os.sep) if chain_dir else []
    for i, part in enumerate(parts):
        if part == "projects" and i + 1 < len(parts):
            return parts[i + 1]
    return None


class ProjectContextProvider(ContextProvider):
    """Capa: contexto y convenciones del proyecto.

    Lee project.json del proyecto activo y extrae convenciones
    arquitectónicas, de codificación y reglas del proyecto.
    """

    def instructions(self, config: dict, ctx: Any) -> str:
        chain_dir = getattr(ctx, "chain_dir", None)
        if not chain_dir or (
            isinstance(chain_dir, str) and (".walker/sessions" in chain_dir or ".cognitive/sessions" in chain_dir)
        ):
            return ""
        return "Layer: project — architecture style, coding conventions, and design rules of this project. Follow these conventions in ALL work."

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        chain_dir = getattr(ctx, "chain_dir", None)

        if not chain_dir or (
            isinstance(chain_dir, str) and (".walker/sessions" in chain_dir or ".cognitive/sessions" in chain_dir)
        ):
            return self._list_all_projects()

        project_id = config.get("project_id", "") or _derive_project_id(chain_dir)
        if not project_id:
            return self._list_all_projects()

        candidate_base = os.path.dirname(os.path.dirname(chain_dir))
        candidates = [
            os.path.join(candidate_base, "project.json"),
        ]
        workspace = getattr(ctx, "workspace", getattr(ctx, "repo_path", None))
        if workspace:
            candidates.append(
                os.path.join(
                    workspace, ".cognitive", "projects", project_id, "project.json"
                )
            )

        proj: dict | None = None
        for path in candidates:
            if os.path.exists(path):
                try:
                    with open(path) as f:
                        data = json.load(f)
                    proj = data.get("project", data)
                    break
                except (json.JSONDecodeError, OSError):
                    continue

        if not proj:
            return None

        parts: list[str] = []
        parts += self._identity_parts(proj, project_id)
        parts += self._location_parts(proj)
        parts += self._design_parts(proj)
        parts += self._coding_parts(proj)
        parts += self._architecture_parts(proj)

        content = "\n".join(parts)
        return ContextPiece(
            provider="project",
            name=f"project context ({project_id})",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=fmt_tokens(content),
            priority=1,
        )

    @staticmethod
    def _identity_parts(proj: dict, project_id: str) -> list[str]:
        """Partes de identidad del proyecto: nombre/language/framework."""
        parts: list[str] = []
        identity = proj.get("identity", {})
        name = identity.get("name", project_id)
        lang = identity.get("language", "")
        framework = identity.get("framework", "")
        parts.append(f"project: {name}")
        if lang:
            parts.append(f"language: {lang}")
        if framework:
            parts.append(f"framework: {framework}")
        return parts

    @staticmethod
    def _location_parts(proj: dict) -> list[str]:
        """Partes de ubicacion: repo root y code_paths."""
        parts: list[str] = []
        loc = proj.get("location", {})
        repo = loc.get("repo_path", "")
        code_paths = loc.get("code_paths", [])
        if repo:
            parts.append(f"root: {repo}")
        if code_paths:
            parts.append(f"code_paths: {', '.join(code_paths[:5])}")
        return parts

    @staticmethod
    def _design_parts(proj: dict) -> list[str]:
        """Partes de diseno: patterns, layers, forbidden, decisions."""
        parts: list[str] = []
        design = proj.get("design", {})
        patterns = design.get("patterns", [])
        if patterns:
            parts.append(f"architecture: {', '.join(patterns)}")

        layers = design.get("layer_rules", {})
        if layers:
            layer_desc = []
            for layer, dirs in layers.items():
                layer_desc.append(f"{layer}=[{','.join(dirs[:3])}]")
            parts.append(f"layers: {' '.join(layer_desc)}")

        forbidden = design.get("forbidden_dependencies", [])
        if forbidden:
            for rule in forbidden[:5]:
                parts.append(f"rule: {rule}")

        decisions = design.get("decisions", [])
        if decisions:
            for d in decisions[:3]:
                title = d.get("title", "")
                decision = d.get("decision", "")
                if title and decision:
                    summary = (
                        decision[:120] + "..." if len(decision) > 120 else decision
                    )
                    parts.append(f"adr: {title} — {summary}")
        return parts

    @staticmethod
    def _coding_parts(proj: dict) -> list[str]:
        """Partes de convenciones de codigo."""
        parts: list[str] = []
        coding = proj.get("coding", {})
        coding_items = []
        for key in (
            "naming_convention",
            "formatter",
            "linter",
            "test_framework",
            "type_checker",
            "import_style",
            "docstring_style",
            "max_line_length",
        ):
            val = coding.get(key)
            if val:
                label = (
                    key.replace("_convention", "")
                    .replace("_framework", "")
                    .replace("_style", "")
                    .replace("_", " ")
                )
                coding_items.append(f"{label}={val}")
        if coding_items:
            parts.append(f"coding: {' '.join(coding_items)}")
        return parts

    @staticmethod
    def _architecture_parts(proj: dict) -> list[str]:
        """Partes de arquitectura (module organization)."""
        parts: list[str] = []
        arch = proj.get("architecture", {})
        module_org = arch.get("module_organization", "")
        if module_org:
            parts.append(f"modules: {module_org[:200]}")
        return parts

    def _list_all_projects(self) -> ContextPiece | None:
        """Lista todos los proyectos disponibles (modo bootstrap/START)."""
        try:
            from features.project import ProjectService
            projects = ProjectService.list_available()
        except Exception:
            return None

        if not projects:
            return ContextPiece(
                provider="project",
                name="projects list",
                instructions="Layer: project — list of available projects.",
                content="(no projects found)",
                tokens=3,
                priority=10,
            )

        lines = ["Available projects:"]
        for p in projects:
            pid = p["project_id"]
            active = [s for s in p["sprints"] if s["status"] == "active"]
            if active:
                lines.append(f"  🟢 {pid} — active sprint: {active[0]['id'][:55]}")
            elif p["sprints"]:
                lines.append(f"  ⚪ {pid} — {len(p['sprints'])} sprints (none active)")
            else:
                lines.append(f"  ⚪ {pid} — (no sprints)")

        lines.append("")
        lines.append("Use 'submit --decision select --project_id <id>' to pick one.")
        lines.append("Use 'submit --decision create' to start a new project.")

        content = "\n".join(lines)
        return ContextPiece(
            provider="project",
            name="projects list",
            instructions="",
            content=content,
            tokens=fmt_tokens(content),
            priority=10,
        )


def register_providers(registry: Any) -> None:
    """Registra los providers de project en un ContextProviderRegistry."""
    registry.register("project", ProjectContextProvider())
