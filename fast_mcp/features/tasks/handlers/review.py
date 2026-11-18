"""handlers/review.py — acción IMPLEMENT.CODE_REVIEW_SCAN: review_code.

La revisión tiene dos capas y las dos importan:

1. **Sintaxis** (barata): ``py_compile`` para ``.py`` y ``node --check`` para
   ``.js``/``.mjs``/``.cjs``. Es la precondición: si el archivo no compila, el
   resto del informe no sirve.
2. **Estado del código (CPG)**: ``ReviewCodeUseCase`` lee el grafo ya indexado
   y devuelve métricas, quality gates, dead code y el segundo informe de bugs.
   Esto es lo que el prompt de ``CODE_REVIEW_SCAN`` promete y lo que el agente
   necesita para decidir si refactoriza.

Regla de ``passed``: fallan los hallazgos **de los archivos de la tarea**
(sintaxis + CPG filtrado a esos paths). Los gates del proyecto se reportan como
``advisory``: ``dead_code`` se calcula sobre todo el repo y hoy arrastra falsos
positivos (``server.add_tool``, ``Path``), así que no puede bloquear una tarea
que no tocó esos archivos.
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


def _syntax_findings(chain_dir: Path, files: list[str]) -> list[dict[str, str]]:
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
    return findings


def _is_task_file(value: str, chain_dir: Path, files: list[str]) -> bool:
    candidate = str(value or "")
    if not candidate:
        return False
    resolved = Path(candidate)
    if not resolved.is_absolute():
        resolved = chain_dir / candidate
    try:
        resolved = resolved.resolve()
    except OSError:
        return False
    for rel in files:
        target = Path(rel)
        if not target.is_absolute():
            target = chain_dir / rel
        try:
            if target.resolve() == resolved:
                return True
        except OSError:
            continue
    return False


def _cpg_review(ctx: ExecutionContext, chain_dir: Path, files: list[str]) -> dict[str, Any]:
    """Estado del código desde el CPG. Fail-open: si no está, se dice por qué.

    Se usa el PUERTO inyectado (`ctx.cpg_adapter`), no un import directo a
    `features.code_analysis`: las features no se importan entre sí (R1).
    """
    if not files:
        return {"available": False, "error": "sin archivos de tarea para revisar"}
    adapter = getattr(ctx, "cpg_adapter", None)
    if adapter is None:
        return {"available": False, "error": "cpg_adapter no inyectado"}
    try:
        absolute = [
            str((chain_dir / rel) if not Path(rel).is_absolute() else Path(rel))
            for rel in files
        ]
        report = adapter.review_code(files=absolute)
    except Exception as exc:  # noqa: BLE001 — la revisión no puede tumbar el paso
        logger.warning("review_code: CPG no disponible (%s)", exc)
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}

    scoped: dict[str, list[dict[str, Any]]] = {}
    total = 0
    for kind, items in (report.get("findings") or {}).items():
        if not isinstance(items, list):
            continue
        total += len(items)
        mine = [
            item
            for item in items
            if isinstance(item, dict) and _is_task_file(str(item.get("file") or ""), chain_dir, files)
        ]
        if mine:
            scoped[kind] = mine[:20]

    second = report.get("second_report") or {}
    bug_findings = [
        item
        for item in (second.get("findings") or [])
        if isinstance(item, dict) and _is_task_file(str(item.get("file") or ""), chain_dir, files)
    ]

    return {
        "available": True,
        "summary": report.get("summary", ""),
        "files_scanned": report.get("files_scanned") or [],
        # Métricas del proyecto (el CPG las calcula sobre todo el repo; hoy las
        # derivadas del grafo vienen en 0 — se reportan tal cual, no se maquillan).
        "metrics": report.get("metrics") or {},
        "gates": [
            {**gate, "scope": "project", "advisory": True}
            for gate in (report.get("gates") or [])
            if isinstance(gate, dict)
        ],
        "findings": scoped,
        "issues": [
            issue
            for issue in (report.get("issues") or [])
            if isinstance(issue, dict) and _is_task_file(str(issue.get("file") or ""), chain_dir, files)
        ],
        "bug_findings": bug_findings[:20],
        "project_findings": total,
        "bug_findings_total": int(second.get("total_findings") or 0),
    }


def review_code(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = Path(str(getattr(ctx, "chain_dir", "") or data.get("chain_dir") or ""))
    files = _files(ctx, data, str(chain_dir))

    syntax = _syntax_findings(chain_dir, files)
    cpg = _cpg_review(ctx, chain_dir, files)
    cpg_findings = sum(len(v) for v in (cpg.get("findings") or {}).values()) if cpg.get("available") else 0
    cpg_issues = len(cpg.get("issues") or []) if cpg.get("available") else 0

    passed = not syntax and cpg_findings == 0 and cpg_issues == 0
    if not cpg.get("available"):
        summary = (
            f"{len(files)} archivos: sintaxis {'ok' if not syntax else 'con errores'}; "
            f"CPG no disponible ({cpg.get('error', 'sin detalle')})"
        )
    else:
        gate = next(
            (g for g in (cpg.get("gates") or []) if not g.get("passed")),
            None,
        )
        tail = (
            f"; gate del proyecto {gate.get('name')}={gate.get('actual')} (advisory)"
            if gate
            else ""
        )
        summary = (
            f"{len(files)} archivos: sintaxis {'ok' if not syntax else 'con errores'}; "
            f"CPG {cpg_findings} hallazgo(s) en los archivos de la tarea"
            f"{tail}"
        )

    return TaskResult.success(
        data={
            "passed": passed,
            "findings": syntax + [
                {"source": "cpg", "type": kind, **item}
                for kind, items in (cpg.get("findings") or {}).items()
                for item in items
            ],
            "summary": summary,
            "cpg": cpg,
        }
    )


def register(registry) -> None:
    registry.register(
        "review_code",
        review_code,
        metadata={
            "description": "Syntax check + CPG code review (metrics, gates, dead code)",
            "tags": ["review", "quality", "cpg"],
            "scope": "internal",
        },
    )
