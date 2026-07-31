"""engine/context/providers.py — Providers de utilidad GENÉRICOS del engine.

PRINCIPIO DE ARQUITECTURA: El engine es un MECANISMO PURO (el camión).
No conoce las capas de negocio (CPG, CKG, Business, State, Delta, Trace).
Los providers de negocio viven en sus features:
  - code_analysis/context/providers.py   → cpg, business
  - cognitive_knowledge/context/providers.py → ckg
  - spring/context/providers.py          → state
  - event_tracing/context/providers.py   → trace
  - delta_tracking/context/providers.py  → delta
  - project/context/providers.py         → project

AQUÍ SOLO viven providers de utilidad genérica (diff, test_results)
que no pertenecen a ninguna feature de negocio.
"""
from __future__ import annotations

import re
import subprocess
from typing import Any

from .models import ContextPiece
from .registry import ContextProvider
from .format import fmt_tokens


class DiffProvider(ContextProvider):
    """Capa: diff estructurado del código modificado (utilidad genérica).

    Proporciona archivos modificados, líneas añadidas/eliminadas,
    funciones cambiadas. Lee git diff desde el workspace.
    """

    def instructions(self, config: dict, ctx: Any) -> str:
        return "Layer: diff — code changes (files changed, lines added/removed, modified functions). Use for code review."

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        workspace = getattr(ctx, "workspace", getattr(ctx, "repo_path", None))
        if not workspace:
            return None

        base_ref = config.get("base_ref", "HEAD~1")

        try:
            stat_result = subprocess.run(
                ["git", "diff", "--stat", f"{base_ref}..HEAD"],
                capture_output=True,
                text=True,
                cwd=workspace,
                timeout=10,
            )
            stat_output = stat_result.stdout.strip()

            diff_result = subprocess.run(
                ["git", "diff", f"{base_ref}..HEAD", "--", "*.py"],
                capture_output=True,
                text=True,
                cwd=workspace,
                timeout=10,
            )
            diff_output = diff_result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

        if not stat_output:
            return ContextPiece(
                provider="diff",
                name="code diff",
                instructions=self.instructions(config, ctx),
                content="(no changes detected)",
                tokens=5,
                priority=25,
            )

        parts = [stat_output.split("\n")[0]]

        files = []
        for line in stat_output.split("\n")[1:]:
            line = line.strip()
            if line and not line.startswith(" ") and not line.startswith("\t"):
                fname = line.split("|")[0].strip()
                if fname:
                    files.append(fname)

        if files:
            parts.append("")
            parts.append("files:")
            for f in files[:15]:
                parts.append(f"  • {f}")
            if len(files) > 15:
                parts.append(f"  ... (+{len(files) - 15} more)")

        if diff_output:
            parts.append("")
            parts.append("changes (key functions):")
            for line in diff_output.split("\n"):
                clean = line.lstrip("+").strip()
                if clean.startswith(("def ", "class ", "    def ", "    class ")):
                    parts.append(f"  +{clean}")

        content = "\n".join(parts)
        return ContextPiece(
            provider="diff",
            name="code diff",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=fmt_tokens(content),
            priority=25,
        )


class TestResultsProvider(ContextProvider):
    """Capa: resultados estructurados de la ejecución de tests (utilidad genérica)."""

    def instructions(self, config: dict, ctx: Any) -> str:
        return "Layer: test_results — structured test output (passed/failed, coverage, error messages). Use for QA decisions."

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        if query_manager is None:
            return None

        result = query_manager.query_layer("trace", {"event": "qa_run"})
        if not result.get("ok", False):
            return None

        events = []
        for items in result.get("data", {}).get("results", {}).values():
            events.extend(items)

        if not events:
            return None

        latest = events[-1]
        data = latest.get("data", {})

        parts = []
        exit_code = data.get("exit_code", "?")
        stdout = data.get("stdout", "")

        parts.append(f"exit_code: {exit_code}")

        passed = 0
        failed = 0
        errors = 0
        for line in stdout.split("\n"):
            nums = re.findall(r"(\d+)\s+(passed|failed|error)", line)
            for num, status in nums:
                if status == "passed":
                    passed = int(num)
                elif status == "failed":
                    failed = int(num)
                elif status == "error":
                    errors = int(num)

        total = passed + failed + errors
        parts.append(
            f"total: {total} • passed: {passed} • failed: {failed} • errors: {errors}"
        )

        if failed > 0 or errors > 0:
            in_failures = False
            failure_lines = []
            for line in stdout.split("\n"):
                if "FAILURES" in line or "FAILED" in line:
                    in_failures = True
                if in_failures:
                    failure_lines.append(line)
                    if len(failure_lines) > 30:
                        break

            if failure_lines:
                parts.append("")
                parts.append("failures:")
                for fl in failure_lines[:25]:
                    parts.append(f"  {fl}")

        content = "\n".join(parts)
        return ContextPiece(
            provider="test_results",
            name="test results",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=fmt_tokens(content),
            priority=30,
        )


def register_providers(registry: Any) -> None:
    """Registra los providers genéricos del engine en un ContextProviderRegistry."""
    registry.register("diff", DiffProvider())
    registry.register("test_results", TestResultsProvider())
