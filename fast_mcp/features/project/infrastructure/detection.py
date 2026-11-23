"""infrastructure.detection — Detección de propiedades del proyecto.

Port de features.project.ProjectDetector (versión autónoma stdlib): marcadores
de repo (pyproject.toml, package.json, Cargo.toml, go.mod, pom.xml) y
frameworks conocidos.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def detect_project(project_path: str) -> dict:
    """Detecta (language, framework) por archivos marcadores del repo."""
    p = Path(project_path)
    language = "unknown"
    framework = "unknown"

    # PureScript primero: un proyecto Spago suele tener también package.json
    # (Metro/Expo), pero el 82% del código puede ser .purs. Medido 2026-11-22:
    # client-chat quedó registrado como "javascript".
    if (p / "spago.yaml").is_file() or (p / "spago.dhall").is_file():
        language = "purescript"
    elif (p / "pyproject.toml").is_file() or (p / "requirements.txt").is_file():
        language = "python"
    elif (p / "package.json").is_file():
        language = "javascript"
    elif (p / "Cargo.toml").is_file():
        language = "rust"
    elif (p / "go.mod").is_file():
        language = "go"
    elif (p / "pom.xml").is_file() or (p / "build.gradle").is_file():
        language = "java"

    if (p / "package.json").is_file():
        framework = _js_framework(p / "package.json")
    elif language == "python":
        if (p / "manage.py").is_file():
            framework = "django"
        elif _mentions(p / "pyproject.toml", "fastapi"):
            framework = "fastapi"

    return {"language": language, "framework": framework}


def _js_framework(pkg_path: Path) -> str:
    try:
        pkg = json.loads(pkg_path.read_text())
    except Exception:  # noqa: BLE001
        return "unknown"
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    for name, fw in (
        ("react-native", "react-native"),
        ("next", "next"),
        ("react", "react"),
        ("vue", "vue"),
        ("express", "express"),
    ):
        if name in deps:
            return fw
    return "unknown"


def _mentions(file: Path, token: str) -> bool:
    try:
        return token in file.read_text()
    except Exception:  # noqa: BLE001
        return False


_CODE_EXTS = (
    ".py", ".js", ".ts", ".rs", ".go", ".java", ".tsx", ".jsx", ".css", ".json", ".md",
)
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".cognitive", "data"}


def code_detected_walk(project_path: str) -> bool:
    """¿Hay código fuente en el repo? (walk simple; CPG real llega en Fase 2)."""
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in files:
            if f.endswith(_CODE_EXTS):
                return True
    return False
