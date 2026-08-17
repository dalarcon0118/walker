"""use_cases/detect_project_context.py — DetectProjectContextUseCase.

Detecta el contexto del proyecto: lenguajes, frameworks, git, code paths.
"""
from __future__ import annotations

import json
import os


class DetectProjectContextUseCase:
    """Detect project context: languages, frameworks, git, code paths."""

    @staticmethod
    def execute(project_path: str) -> dict:
        has_git = os.path.isdir(os.path.join(project_path, ".git"))
        languages = set()
        framework = "unknown"
        code_paths = []

        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
            for f in files:
                if f.endswith(".py"):
                    languages.add("python")
                elif f.endswith((".js", ".jsx")):
                    languages.add("javascript")
                elif f.endswith((".ts", ".tsx")):
                    languages.add("typescript")
                elif f.endswith(".rs"):
                    languages.add("rust")
                elif f.endswith(".java"):
                    languages.add("java")
                elif f.endswith(".go"):
                    languages.add("go")
                if f == "package.json":
                    try:
                        with open(os.path.join(root, f)) as pf:
                            framework = "node/" + json.load(pf).get("name", "unknown")
                    except Exception:
                        framework = "node"
                elif f == "Cargo.toml":
                    framework = "rust/cargo"
                elif f == "go.mod":
                    framework = "go"
            if root == project_path:
                code_paths = [
                    d
                    for d in dirs
                    if d not in (".git", "__pycache__", "node_modules", "venv")
                ]
                break

        return {
            "detected": True,
            "path": project_path,
            "has_git": has_git,
            "has_code": len(languages) > 0,
            "languages": list(languages),
            "framework": framework,
            "code_paths": code_paths,
        }
