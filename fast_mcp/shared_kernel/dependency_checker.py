"""
features/dependency_checker.py — CLI tool verification & auto-install.

Walker3 needs external CLI tools (ast-grep) for multi-language code
analysis. This module checks them at runtime and auto-installs if missing
(controlled by WALKER_AUTO_INSTALL). qlty fue eliminado (ADR-WALKER-054):
las métricas son CPG-nativas, no requieren binario externo.

Usage:
    from fast_mcp.shared_kernel.dependency_checker import ensure_tools, ensure_ast_grep

    ensure_tools(["ast-grep"])      # Check & install if missing
    ensure_ast_grep()               # Shorthand
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

# ── Config ────────────────────────────────────────────────────
# WALKER_AUTO_INSTALL=0  → warn only, don't install
# WALKER_AUTO_INSTALL=1  → auto-install (default)
_AUTO_INSTALL = os.environ.get("WALKER_AUTO_INSTALL", "1") not in ("0", "no", "false")

# Verification cache to avoid repeated `which` calls
_cache: dict[str, bool] = {}


# ── Public API ────────────────────────────────────────────────
def is_available(tool: str) -> bool:
    """Check if a CLI tool is available in PATH (cached)."""
    if tool not in _cache:
        _cache[tool] = shutil.which(tool) is not None
    return _cache[tool]


def reset_cache() -> None:
    """Clear the cache (useful after installing something)."""
    _cache.clear()


def ensure_tools(tools: list[str]) -> list[str]:
    """Check that tools are installed.

    If any are missing and WALKER_AUTO_INSTALL is active, installs them.
    If installation fails, prints a warning.

    Returns:
        List of tools still missing after the attempt.
    """
    missing = [t for t in tools if not is_available(t)]
    if not missing:
        return []

    print(f"  🔧 Walker3: missing CLI tools: {', '.join(missing)}", file=sys.stderr)

    if not _AUTO_INSTALL:
        _print_manual_instructions(missing)
        return missing

    for tool in missing:
        _install_tool(tool)

    reset_cache()
    still_missing = [t for t in missing if not is_available(t)]

    if still_missing:
        print(
            f"  ⚠️  Walker3: failed to install: {', '.join(still_missing)}",
            file=sys.stderr,
        )
        _print_manual_instructions(still_missing)

    return still_missing


def ensure_ast_grep() -> bool:
    """Check/install ast-grep. Returns True if available."""
    return not ensure_tools(["ast-grep"])


def ensure_all() -> list[str]:
    """Check/install ALL known tools."""
    return ensure_tools(["ast-grep"])


def check_and_report(verbose: bool = False) -> int:
    """Check all tools and print a report.

    Returns:
        Number of missing tools.
    """
    tools = {
        "ast-grep": "Multi-language code analysis (CPG outline, CALLS, security)",
    }

    missing = 0
    for tool, desc in tools.items():
        available = is_available(tool)
        status = "✅" if available else "❌"
        version = ""
        if available:
            try:
                r = subprocess.run(
                    [tool, "--version"], capture_output=True, text=True, timeout=5
                )
                version = f" ({r.stdout.strip() or r.stderr.strip()})"
            except Exception:
                pass

        label = f"  {status} {tool}{version}"
        if verbose:
            print(f"{label}")
            print(f"     {desc}")
        else:
            print(f"{label}")

        if not available:
            missing += 1

    return missing


# ── Installers ────────────────────────────────────────────────
def _install_ast_grep() -> bool:
    """Install ast-grep. Returns True on success."""
    print("  → Installing ast-grep...", file=sys.stderr)

    # macOS via Homebrew (preferred, fast)
    if sys.platform == "darwin" and shutil.which("brew"):
        try:
            print("    Running: brew install ast-grep", file=sys.stderr)
            subprocess.run(
                ["brew", "install", "ast-grep"],
                check=True,
                timeout=120,
            )
            reset_cache()
            if is_available("ast-grep"):
                print("  ✅ ast-grep installed via brew", file=sys.stderr)
                return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            print("    brew failed, trying npm...", file=sys.stderr)

    # npm global
    if shutil.which("npm"):
        try:
            subprocess.run(
                ["npm", "install", "-g", "@ast-grep/cli"],
                check=True,
                timeout=120,
            )
            reset_cache()
            if is_available("ast-grep"):
                print("  ✅ ast-grep installed via npm", file=sys.stderr)
                return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            print("    npm failed, trying cargo...", file=sys.stderr)

    # cargo (fallback, slow)
    if shutil.which("cargo"):
        try:
            print("    This may take a few minutes (cargo compile)...", file=sys.stderr)
            subprocess.run(
                ["cargo", "install", "ast-grep", "--locked"],
                check=True,
                timeout=600,
            )
            reset_cache()
            if is_available("ast-grep"):
                print("  ✅ ast-grep installed via cargo", file=sys.stderr)
                return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass

    print("  ❌ Failed to install ast-grep automatically.", file=sys.stderr)
    return False


def _install_tool(tool: str) -> bool:
    """Install a specific tool."""
    installers = {
        "ast-grep": _install_ast_grep,
    }
    installer = installers.get(tool)
    if installer:
        return installer()
    return False


def _print_manual_instructions(tools: list[str]) -> None:
    """Print manual installation instructions."""
    instructions = {
        "ast-grep": (
            "  brew install ast-grep\n"
            "  npm install -g @ast-grep/cli\n"
            "  https://ast-grep.github.io/"
        ),
    }
    print("\n  📋 Manual installation:", file=sys.stderr)
    for tool in tools:
        if tool in instructions:
            print(f"\n    {tool}:", file=sys.stderr)
            print(instructions[tool], file=sys.stderr)
    print(file=sys.stderr)
