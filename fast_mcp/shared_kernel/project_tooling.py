"""shared_kernel/project_tooling.py — detección de tooling desde evidencia.

Helper PURO (solo stdlib): lee los archivos del repo y devuelve
`test_framework`/`formatter`/`linter` cuando hay evidencia. Lo comparten
`prepare` (features/project) y la acción BUILD_PROJECT (features/tasks) sin que
una feature importe a la otra (R1).

Regla: sin evidencia, la clave no aparece. Nunca se inventa un default: un
campo vacío significa "no detectado", y el llamador decide si lo propone.
"""
from __future__ import annotations

from pathlib import Path


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def detect_tooling(root: str | Path, language: str) -> dict[str, str]:
    """Test framework / formatter / linter detectados por evidencia.

    Args:
        root: raíz del repo (donde viven pyproject.toml/package.json).
        language: lenguaje detectado (python/javascript/typescript/...).

    Returns:
        dict con las claves encontradas (puede ser vacío).
    """
    base = Path(root)
    pyproject = _read(base / "pyproject.toml").lower()
    pkg = _read(base / "package.json").lower()
    reqs = " ".join(
        _read(base / name).lower()
        for name in ("requirements.txt", "requirements-dev.txt", "setup.cfg", "tox.ini")
    )
    blob = f"{pyproject} {reqs}"
    out: dict[str, str] = {}

    # PureScript: `spago.yaml` (Spago 0.93+) es la evidencia del runner, sin
    # importar el lenguaje detectado por package.json. Sin esto, un repo
    # PureScript quedaba con `test_framework` vacío y `run_tests` no corría.
    if (base / "spago.yaml").is_file() or (base / "spago.dhall").is_file():
        out["test_framework"] = "spago"

    if language == "python":
        if (
            (base / "pytest.ini").exists()
            or (base / "conftest.py").exists()
            or (base / "tests").is_dir()
            or "[tool.pytest" in pyproject
            or "pytest" in blob
        ):
            out["test_framework"] = "pytest"
        if "ruff" in blob or (base / "ruff.toml").exists():
            out["linter"] = "ruff"
            out["formatter"] = "ruff"
        elif "black" in blob:
            out["formatter"] = "black"
        if "linter" not in out:
            if "flake8" in blob or (base / ".flake8").exists():
                out["linter"] = "flake8"
            elif "pylint" in blob:
                out["linter"] = "pylint"
    elif language in ("javascript", "typescript"):
        if "test_framework" not in out:
            if "vitest" in pkg:
                out["test_framework"] = "vitest"
            elif "jest" in pkg:
                out["test_framework"] = "jest"
            elif "mocha" in pkg:
                out["test_framework"] = "mocha"
        if "prettier" in pkg or any(
            (base / n).exists() for n in (".prettierrc", ".prettierrc.json")
        ):
            out["formatter"] = "prettier"
        if "eslint" in pkg or any(
            (base / n).exists() for n in (".eslintrc", ".eslintrc.json", ".eslintrc.js")
        ):
            out["linter"] = "eslint"
    return out
