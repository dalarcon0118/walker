"""use_cases/project_detector.py — ProjectDetector.

Detección de propiedades del proyecto escaneando el filesystem.
SRP: Pure detection logic — no side effects beyond filesystem reads.
"""
from __future__ import annotations

import json
import os
from typing import Any

# ── Constants ──────────────────────────────────────────────────────────────

IGNORE_DIRS = frozenset(
    {
        "node_modules",
        "__pycache__",
        ".git",
        ".expo",
        ".venv",
        "venv",
        "dist",
        "build",
        ".next",
        "coverage",
        ".cognitive",
    }
)
CODE_EXTENSIONS = (".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java")


class ProjectDetector:
    """Detect project properties by scanning the filesystem."""

    @staticmethod
    def detect_language(project_path: str) -> str:
        """Detect primary language from file extensions."""
        scores: dict[str, int] = {}
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [
                d for d in dirs if not d.startswith(".") and d not in IGNORE_DIRS
            ]
            for f in files:
                if f.endswith(".py"):
                    scores["python"] = scores.get("python", 0) + 1
                elif f.endswith((".ts", ".tsx")):
                    scores["typescript"] = scores.get("typescript", 0) + 1
                elif f.endswith((".js", ".jsx")):
                    scores["javascript"] = scores.get("javascript", 0) + 1
                elif f.endswith(".rs"):
                    scores["rust"] = scores.get("rust", 0) + 1
                elif f.endswith(".go"):
                    scores["go"] = scores.get("go", 0) + 1
                elif f.endswith(".java"):
                    scores["java"] = scores.get("java", 0) + 1
        return max(scores, key=scores.get) if scores else "unknown"

    @staticmethod
    def detect_framework(project_path: str) -> str:
        """Detect framework from config files."""
        pkg_json = os.path.join(project_path, "package.json")
        if os.path.exists(pkg_json):
            try:
                with open(pkg_json) as f:
                    pkg = json.load(f)
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "react-native" in deps:
                    return "react-native"
                if "next" in deps:
                    return "next.js"
                if "react" in deps:
                    return "react"
                if "vue" in deps:
                    return "vue"
                if "@angular/core" in deps:
                    return "angular"
            except Exception:
                pass
        if os.path.exists(os.path.join(project_path, "Cargo.toml")):
            return "rust/cargo"
        if os.path.exists(os.path.join(project_path, "go.mod")):
            return "go"
        if os.path.exists(os.path.join(project_path, "Gemfile")):
            return "ruby/rails"
        return "unknown"

    @staticmethod
    def detect_code_paths(project_path: str) -> list[str]:
        """[DEPRECADO — ADR-WALKER-054 D1] El scope es repo_path completo.

        Se conserva por compatibilidad de firma; el concepto fue eliminado
        del manifest. Devuelve la lista vacía.
        """
        return []

    @classmethod
    def detect_all(cls, project_path: str) -> dict[str, Any]:
        """Run all detections at once."""
        return {
            "language": cls.detect_language(project_path),
            "framework": cls.detect_framework(project_path),
            "code_paths": [],
        }
