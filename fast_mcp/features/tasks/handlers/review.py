"""handlers/review.py — acción IMPLEMENT.CODE_REVIEW_SCAN: review_code.

Revisión real y barata: ``py_compile`` para ``.py`` y ``node --check`` para
``.js``/``.mjs``/``.cjs``. Los archivos vienen del submit o de la spec de la
tarea; sin archivos devuelve ``passed`` (no hay nada que revisar).
"""
from __future__ import annotations

import logging
import py_compile
import subprocess
from pathlib import Path
from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult

logger = logging.getLogger(__name__)


def _files(ctx: ExecutionContext, data: dict[str, Any], chain_dir: str) -> list[str]:
    files = data.get("files")
    if isinstance(files, list) and files:
        return [str(f) for f in files]
    try:
        from fast_mcp.features.tasks.handlers import _di

        spec = _di.current_spec(
            chain_dir,
            task_id=str(getattr(ctx, "task_id", "") or data.get("task_id") or ""),
        )
        if spec:
            return [str(f) for f in (spec.get("files") or [])]
    except Exception:  # noqa: BLE001 — sin spec se devuelve sin archivos
        pass
    return []


def review_code(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = Path(str(getattr(ctx, "chain_dir", "") or data.get("chain_dir") or ""))
    files = _files(ctx, data, str(chain_dir))
    findings: list[dict[str, str]] = []

    for rel in files:
        path = Path(rel)
        if not path.is_absolute():
            path = chain_dir / rel
        if not path.exists():
            findings.append({"file": rel, "error": "no existe"})
            continue

        suffix = path.suffix.lower()
        if suffix == ".py":
            try:
                py_compile.compile(str(path), doraise=True)
            except py_compile.PyCompileError as exc:
                findings.append({"file": rel, "error": str(exc)[:400]})
        elif suffix in (".js", ".mjs", ".cjs"):
            try:
                proc = subprocess.run(
                    ["node", "--check", str(path)],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                if proc.returncode != 0:
                    findings.append(
                        {"file": rel, "error": (proc.stderr or proc.stdout).strip()[:400]}
                    )
            except FileNotFoundError:
                findings.append({"file": rel, "error": "node no disponible"})
            except Exception as exc:  # noqa: BLE001
                findings.append({"file": rel, "error": f"{type(exc).__name__}: {exc}"})

    return TaskResult.success(
        data={
            "passed": not findings,
            "findings": findings,
            "summary": f"{len(files)} archivos revisados, {len(findings)} hallazgos",
        }
    )


def register(registry) -> None:
    registry.register(
        "review_code",
        review_code,
        metadata={
            "description": "Static review: py_compile / node --check",
            "tags": ["review", "quality"],
            "scope": "internal",
        },
    )
