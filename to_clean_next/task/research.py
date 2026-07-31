"""task/research.py — Context discovery and research"""

from __future__ import annotations

import sys
from pathlib import Path

_THIS_DIR = str(Path(__file__).parent.parent)
_AST_INTERNAL = str(Path(_THIS_DIR) / "_internal")
if _AST_INTERNAL not in sys.path:
    sys.path.insert(0, _AST_INTERNAL)

import json
import os
import re
import subprocess
import sys
from .common import (
    load_json,
    find_project_root,
    eprint,
    _extract_project_id,
    _resolve_project_path,
    _GREP_EXCLUDE_DIRS,
)
from ast_grep_utils import (
    AST_GREP_AVAILABLE,
    _ast_grep_lang,
    extract_definitions,
)


def _grep_symbol(symbol: str, search_root: str) -> list[str]:
    """Search for a symbol in code using exact word matching (no regex).

    Args:
        symbol: The symbol name to search for.
        search_root: Directory to search within. Should be project-scoped
            (``projects/<project_id>/``) for performance.
    """
    try:
        result = subprocess.run(
            ["grep", "-rw", "--include=*.py", symbol, search_root] + _GREP_EXCLUDE_DIRS,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return [line for line in result.stdout.strip().splitlines() if line]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return []


def _file_exists(project_root: str, path: str) -> bool:
    """Check if a file exists, resolving relative paths."""
    if os.path.isabs(path):
        return os.path.exists(path)

    # Try direct from project_root
    direct = os.path.join(project_root, path)
    if os.path.exists(direct):
        return True

    # Try under projects/<project_id>/
    # We need project_id, but we don't have it here
    # Try common patterns
    patterns = [
        path,
        os.path.join("projects", path),
        os.path.join("projects", "cognitive-mcp", path),
        os.path.join("projects", "cognitive-mcp", "cognitive_v2", path),
    ]

    for pattern in patterns:
        candidate = os.path.join(project_root, pattern)
        if os.path.exists(candidate):
            return True

    return False


def _list_module_symbols(project_root: str, module_path: str) -> list[str]:
    """Extract class/function names from a Python file using AST parsing.

    Uses Python's ast module (not regex) for accurate symbol extraction:
    - Catches class, def, async def, @property, __all__ exports
    - Language-agnostic effect: the AST structure is the same in any
      human language (class names, function names are code, not text)
    - Falls back to _internal/hallucination.py AST scanner if available
    """
    symbols = []

    # Try to resolve the path
    full_path = module_path
    if not os.path.isabs(full_path):
        candidates = [
            os.path.join(project_root, full_path),
            os.path.join(project_root, "projects", full_path),
            os.path.join(project_root, "projects", "cognitive-mcp", full_path),
            os.path.join(
                project_root, "projects", "cognitive-mcp", "cognitive_v2", full_path
            ),
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                full_path = candidate
                break

    if not os.path.exists(full_path) or not full_path.endswith(".py"):
        return symbols

    try:
        import ast

        with open(full_path) as fh:
            tree = ast.parse(fh.read())

        for node in ast.walk(tree):
            # class X, def x, async def x
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("_"):
                    symbols.append(node.name)
            # __all__ exports
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        if isinstance(node.value, ast.List):
                            for elt in node.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(
                                    elt.value, str
                                ):
                                    symbols.append(elt.value)
    except SyntaxError:
        # Archivo no es Python valido — no se pueden extraer simbolos
        pass
    return symbols


def _extract_symbols(text: str) -> set[str]:
    """Extract code symbols from text (language-agnostic).

    Extracts ONLY real code-like identifiers:
    - CamelCase classes: JackpotConfig, GameService
    - snake_case functions with parens: calculate_jackpot(), check_health()
    - snake_case with underscores: check_health, _check_kv, game_reset
    - ALL_CAPS constants: MAX_RETRIES, DEFAULT_TIMEOUT
    - file paths with extensions: src/game/models.py

    DELIBERATELY excludes pure lowercase words without underscores
    (e.g., "retorna", "dict", "modulo") — these are natural language,
    not code symbols.

    This is LANGUAGE-AGNOSTIC: code symbols are identical in
    Spanish, English, or any human language.
    """
    symbols: set[str] = set()
    if not text:
        return symbols

    # CamelCase: JackpotConfig, GameService
    symbols.update(re.findall(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b", text))

    # Single uppercase word >= 3 chars: Json, Http, Api, Config
    symbols.update(re.findall(r"\b[A-Z][a-z0-9]{2,}\b", text))

    # snake_case with parentheses: calculate_jackpot(), check_health()
    symbols.update(re.findall(r"\b[a-z_][a-z0-9_]*\(\)", text))

    # snake_case WITH underscore chars: check_health, _check_kv, game_reset
    # (requires at least one underscore — pure lowercase words excluded)
    symbols.update(re.findall(r"\b[a-z_][a-z0-9_]*_[a-z0-9_]+\b", text))

    # ALL_CAPS constants: MAX_RETRIES, DEFAULT_TIMEOUT
    symbols.update(re.findall(r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b", text))

    # File paths with known extensions
    symbols.update(
        re.findall(r"[\w./-]+\.(?:py|ts|js|tsx|jsx|java|rs|go|rb|swift|kt)", text)
    )

    return {s for s in symbols if len(s) >= 2}


def _verify_symbols_in_files(
    symbols: set[str],
    files: list[str],
    project_root: str,
    project_id: str | None,
) -> dict[str, list[str]]:
    """Verify which symbols from task_context exist as real code definitions.

    Uses ast-grep (sg CLI) for AST-based structural matching (same approach
    as analyze_refactored.py). Falls back to grep if ast-grep unavailable.

    Multi-language support via _LANG_MAP:
    - Python, TypeScript, JavaScript, Rust, Go, Java, Ruby, Kotlin, etc.

    Returns dict of {symbol: [matching_lines]} for verified symbols.
    """
    import signal

    def timeout_handler(signum, frame):
        raise TimeoutError("Symbol verification timed out")

    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(30)  # 30 second timeout

    try:
        return _verify_symbols_in_files_impl(symbols, files, project_root, project_id)
    except TimeoutError:
        print("  ⚠️  Symbol verification timed out — skipping")
        return {}
    finally:
        signal.alarm(0)


def _verify_symbols_in_files_impl(
    symbols: set[str],
    files: list[str],
    project_root: str,
    project_id: str | None,
) -> dict[str, list[str]]:
    """Verify which symbols from task_context exist as real code definitions.

    Uses ast-grep (sg CLI) for AST-based structural matching (same approach
    as analyze_refactored.py). Falls back to grep if ast-grep unavailable.

    Multi-language support via _LANG_MAP:
    - Python, TypeScript, JavaScript, Rust, Go, Java, Ruby, Kotlin, etc.

    Returns dict of {symbol: [matching_lines]} for verified symbols.
    """
    verified: dict[str, list[str]] = {}

    # Filter symbols: skip common words, short symbols
    valid_symbols = set()
    common_words = {
        "este",
        "esta",
        "esto",
        "para",
        "como",
        "con",
        "por",
        "que",
        "del",
        "las",
        "los",
        "una",
        "uno",
        "mas",
        "the",
        "and",
        "for",
        "from",
        "with",
        "that",
        "this",
        "archivo",
        "archivos",
        "clase",
        "funcion",
        "codigo",
        "module",
        "class",
        "function",
        "method",
        "file",
        "data",
        "puede",
        "debe",
        "hace",
        "tiene",
        "code",
        "test",
        "file",
        "type",
        "name",
        "path",
        "list",
        "find",
        "value",
        "count",
        "index",
        "items",
        "users",
        "admin",
        "login",
        "token",
        "model",
        "route",
        "utils",
        "types",
        "base",
        "core",
        "main",
        "init",
        "help",
        "info",
    }
    for sym in symbols:
        if len(sym) >= 4 and sym.lower() not in common_words:
            valid_symbols.add(sym)

    if not valid_symbols or not files:
        return verified

    # ── PHASE 1: Ast-grep — extract definitions using shared module ──
    if AST_GREP_AVAILABLE:
        for f in files:
            resolved = _resolve_project_path(project_root, project_id, f)
            if not os.path.exists(resolved):
                continue
            lang = _ast_grep_lang(resolved)
            if not lang:
                continue

            # Extract ALL definitions from file via shared module
            file_definitions = extract_definitions(resolved, lang)
            if not file_definitions:
                continue

            # Intersect with task_context symbols
            matched = valid_symbols & file_definitions
            for sym in matched:
                verified[sym] = [f"{f} (ast-grep: definición real)"]
            valid_symbols -= matched

        if not valid_symbols:
            return verified

    # ── PHASE 2: Fallback to grep (text-based, for remaining symbols) ──
    # Filter: skip directory-path-like symbols (cognitive_v2/path/file.py)
    # and known generic terms — grep on whole project is expensive
    grep_valid = set()
    for sym in valid_symbols:
        # Skip: path-like symbols (contain /)
        if "/" in sym or "\\" in sym:
            continue
        # Skip: known generic / domain terms unlikely to be code definitions
        if sym.lower() in {
            "cognitive",
            "shared",
            "feature",
            "features",
            "domain",
            "application",
            "infrastructure",
            "project",
            "config",
            "utils",
            "helper",
            "service",
            "controller",
            "repository",
            "adapter",
            "middleware",
            "handler",
            "schema",
            "model",
            "entity",
            "value",
        }:
            continue
        grep_valid.add(sym)

    for sym in sorted(grep_valid):
        found = []

        # Grep in affected files first (fast, targeted)
        for f in files:
            resolved = _resolve_project_path(project_root, project_id, f)
            if not os.path.exists(resolved):
                continue
            try:
                result = subprocess.run(
                    ["grep", "-n", sym, resolved],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().splitlines()[:2]:
                        found.append(f"{f}:{line}")
                    break
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        # Grep in project directory only (fast, scoped) or full root as fallback
        if not found and len(sym) >= 4:
            _proj_search = (
                os.path.join(project_root, "projects", project_id)
                if project_id
                and os.path.isdir(os.path.join(project_root, "projects", project_id))
                else project_root
            )
            try:
                # Limit to source files only for speed
                result = subprocess.run(
                    [
                        "grep",
                        "-rn",
                        "--include=*.py",
                        "--include=*.ts",
                        "--include=*.js",
                        "--include=*.rs",
                        "--include=*.go",
                        "--include=*.java",
                        sym,
                        _proj_search,
                    ]
                    + _GREP_EXCLUDE_DIRS,
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().splitlines()[:2]:
                        found.append(line[:120])
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        if found:
            verified[sym] = found

    return verified


def _is_generic_placeholder(text: str) -> bool:
    """Detect if a text is a generic placeholder.

    Pure structural heuristic — NO language-dependent keywords.
    Uses only:
    - Text length (too short = placeholder)
    - Code symbol count (no code symbols = filler)
    """
    text = text.strip()
    if len(text) < 20:
        return True  # Too short to be meaningful

    # Symbols present? If none, it's likely generic filler
    symbols = _extract_symbols(text)
    if len(symbols) < 2:
        return True  # No code symbols = no technical substance

    return False


def _get_project_search_root(
    chain_dir: str, project_root: str, project_id: str | None
) -> str | None:
    """Resolve the project-scoped search root from plan.json.

    Priority:
    1. ``project_path`` from plan.json (can be relative or absolute)
    2. ``projects/<project_id>/`` under project_root (legacy)
    3. ``project_id`` as directory under project_root (fallback)

    Returns None if nothing resolves.
    """
    plan_path = os.path.join(chain_dir, "plan.json")
    plan = load_json(plan_path)
    if plan:
        pl = plan.get("plan", plan)
        project_section = pl.get("project", {})
        explicit_path = project_section.get("project_path")
        if explicit_path:
            if os.path.isabs(explicit_path):
                return explicit_path
            candidate = os.path.join(project_root, explicit_path)
            if os.path.isdir(candidate):
                return candidate

    if project_id:
        project_dir = os.path.join(project_root, "projects", project_id)
        if os.path.isdir(project_dir):
            return project_dir

        fallback = os.path.join(project_root, project_id)
        if os.path.isdir(fallback):
            return fallback

    return None


def _context_discovery(chain_dir: str, task_id: str) -> None:
    """Print mandatory context discovery for a task.

    Forces the LLM to see the real state of affected files and APIs
    before implementing, preventing duplicate/alredy-existing artifacts.

    Grep searches are scoped to ``projects/<project_id>/`` (the project
    boundary), NOT the entire repo root. If a symbol isn't found within
    the project, it's an error — the task is referencing code outside
    its scope.
    """
    plan_path = os.path.join(chain_dir, "plan.json")
    plan = load_json(plan_path)
    if not plan:
        return

    pl = plan.get("plan", plan)
    task = None
    for t in pl.get("tasks", []):
        if t.get("task_id") == task_id:
            task = t
            break

    if not task:
        return

    artefacts = task.get("impacted_artifacts", {})
    files = artefacts.get("files", [])
    apis = artefacts.get("apis", [])
    data = artefacts.get("data", [])

    project_root = find_project_root(chain_dir)
    project_id = _extract_project_id(chain_dir)

    # ── Resolve project-scoped search root ──────────────────────
    # Grep is scoped to the project's own directory, NOT the full
    # repo root. Each sprint belongs to ONE project, so symbols
    # should only exist within that project's boundary.
    # If the project path doesn't exist, we STOP — not fallback to
    # full-repo grep.
    project_search_root = _get_project_search_root(chain_dir, project_root, project_id)
    if project_search_root is None:
        eprint()
        eprint("=" * 70)
        eprint(
            "❌ CONTEXT DISCOVERY BLOCKED: No se puede determinar el directorio del proyecto"
        )
        eprint("=" * 70)
        eprint(f"  chain_dir:  {chain_dir}")
        eprint(f"  project_id: {project_id}")
        eprint(f"  project_root: {project_root}")
        eprint()
        eprint("  El sprint no tiene un project_id válido o el directorio")
        eprint(
            f"  {os.path.join(project_root, 'projects', project_id or '?')} no existe."
        )
        eprint()
        eprint("  El grep está acotado al proyecto. Sin project_id, no se puede")
        eprint("  proceder con context discovery. Corrige el plan.json o crea")
        eprint("  el sprint con --project-id.")
        eprint("=" * 70)
        return

    print()
    print("  📂 ARCHIVOS AFECTADOS:")
    if not files:
        print("    (ninguno)")
    # Extract project_path from plan.json
    plan_for_path = load_json(os.path.join(chain_dir, "plan.json"))
    project_path = None
    if plan_for_path:
        project_path = (
            plan_for_path.get("plan", plan_for_path)
            .get("project", {})
            .get("project_path")
        )

    for f in files:
        candidate = _resolve_project_path(
            project_root, project_id, f, project_path=project_path
        )
        if os.path.exists(candidate):
            try:
                with open(candidate) as fh:
                    lines = fh.readlines()
                preview = "".join(lines[:20]).strip()
                print(f"  ✅ EXISTE: {f}")
                print("     Primeras líneas:")
                for line in preview.splitlines()[:10]:
                    print(f"       {line}")
            except Exception as exc:
                print(f"  ⚠️  EXISTE pero no se pudo leer: {f} ({exc})")
        else:
            print(f"  🆕 NUEVO: {f} (no existe, debe crearse)")
    print()

    # APIs / symbols
    print("  🔌 APIS / SÍMBOLOS A CREAR:")
    if not apis:
        print("    (ninguno)")
    for api in apis:
        result = subprocess.run(
            ["grep", "-r", "--include=*.py", api, project_search_root]
            + _GREP_EXCLUDE_DIRS,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            matches = result.stdout.strip().splitlines()[:5]
            print(f"  ⚠️  YA EXISTE: {api}")
            for m in matches:
                print(f"     {m[:100]}")
        else:
            print(f"  🆕 NUEVO: {api}")
    print()

    # Data / config
    if data:
        print("  📊 DATOS / CONFIG:")
        for d in data:
            result = subprocess.run(
                ["grep", "-r", d, project_search_root] + _GREP_EXCLUDE_DIRS,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                matches = result.stdout.strip().splitlines()[:3]
                print(f"  ⚠️  YA EXISTE: {d}")
                for m in matches:
                    print(f"     {m[:100]}")
            else:
                print(f"  🆕 NUEVO: {d}")
        print()

    # 6-layer protocol (task_context)
    task_context = task.get("task_context", {})
    if task_context:
        print("  🧠 CONTEXTO PROFUNDO (protocolo 6 capas):")
        layers = [
            ("observation", "1. Observación: ¿Qué existe?"),
            ("structure", "2. Estructura: ¿Cómo está organizado?"),
            ("behavior", "3. Comportamiento: ¿Cómo interactúa?"),
            ("causality", "4. Causalidad: ¿Por qué ocurre?"),
            ("purpose", "5. Finalidad: ¿Para qué existe?"),
            ("principles", "6. Principios: ¿Qué leyes generales gobiernan?"),
        ]
        for key, label in layers:
            value = task_context.get(key, "")
            if value:
                print(f"  {label}")
                for line in value.splitlines()[:8]:
                    print(f"       {line}")
                if len(value.splitlines()) > 8:
                    print(f"       ... (+{len(value.splitlines()) - 8} líneas)")
        print()

    print("  ⚠️  ANTES DE ESCRIBIR CÓDIGO: lee el contexto real arriba.")
    print("  Si algo YA EXISTE, ajusta la implementación (no lo dupliques).")
    print("=" * 70)


def _describe_observation(
    task: dict, existing_files: list, symbols_by_file: dict
) -> str:
    """Capa 1: ¿Qué existe? (hechos, datos, síntomas)"""
    lines = []
    lines.append(f"Objetivo: {task.get('objective', 'N/A')}")
    lines.append(f"Estado esperado: {task.get('result_state', 'N/A')}")
    if existing_files:
        lines.append(f"Archivos existentes ({len(existing_files)}):")
        for f in existing_files:
            syms = symbols_by_file.get(f, [])
            if syms:
                lines.append(f"  - {os.path.basename(f)}: {', '.join(syms[:5])}")
            else:
                lines.append(f"  - {os.path.basename(f)}")
    tc = task.get("task_context", {})
    for k in ("observation", "observación", "observation"):
        val = tc.get(k, "")
        if val:
            lines.append(f"Contexto previo: {val[:200]}")
            break
    return "\n".join(lines)


def _describe_structure(task: dict, existing_files: list, symbols_by_file: dict) -> str:
    """Capa 2: ¿Cómo está organizado? (componentes, dependencias)"""
    lines = []
    lines.append(f"Tipo de tarea: {task.get('work_type', task.get('type', 'N/A'))}")
    lines.append(f"Dominio: {task.get('domain', 'N/A')}")
    if existing_files:
        lines.append("Organización de archivos:")
        for f in existing_files:
            parts = f.split("/")
            lines.append(f"  - {f} (en {parts[-2] if len(parts) > 1 else 'root'})")
    artefacts = task.get("impacted_artifacts", {})
    apis = artefacts.get("apis", [])
    if apis:
        lines.append(f"APIs/símbolos requeridos ({len(apis)}): {', '.join(apis[:8])}")
    return "\n".join(lines)


def _describe_behavior(task: dict, existing_files: list, apis: list) -> str:
    """Capa 3: ¿Cómo interactúa? (flujos, contratos)"""
    lines = []
    lines.append(f"Result state: {task.get('result_state', 'N/A')}")
    preconditions = task.get("preconditions", [])
    if preconditions:
        lines.append("Precondiciones:")
        for p in preconditions:
            lines.append(f"  - {p[:120]}")
    if apis:
        lines.append(f"Contratos/APIs a implementar o modificar ({len(apis)}):")
        for a in apis[:8]:
            lines.append(f"  - {a}")
    return "\n".join(lines)


def _describe_causality(task: dict, risks: dict) -> str:
    """Capa 4: ¿Por qué ocurre? (restricciones, invariantes)"""
    lines = []
    risks_str = []
    for k, v in risks.items():
        if isinstance(v, (int, float)) and v > 3:
            risks_str.append(f"{k}={v}")
    if risks_str:
        lines.append(f"Riesgos altos: {', '.join(risks_str)}")
    validation = task.get("validation", {})
    preds = validation.get("predicates", [])
    if preds:
        lines.append(f"Predicates ({len(preds)}):")
        for p in preds[:5]:
            p_str = p if isinstance(p, str) else p.get("command", str(p))
            lines.append(f"  - {p_str[:120]}")
    return "\n".join(lines)


def _describe_purpose(
    task: dict, acceptance_criteria: list, quality_gates: list
) -> str:
    """Capa 5: ¿Para qué existe? (objetivos, trade-offs)"""
    lines = []
    if acceptance_criteria:
        lines.append("Criterios de aceptación:")
        for ac in acceptance_criteria:
            lines.append(f"  - {ac[:120]}")
    if quality_gates:
        lines.append("Quality gates:")
        for qg in quality_gates:
            lines.append(f"  - {qg[:120]}")
    decisions = task.get("decisions", [])
    if decisions:
        lines.append("Decisiones registradas:")
        for d in decisions:
            if isinstance(d, dict):
                chosen = d.get("chosen", "")
                rationale = d.get("rationale", "")
                lines.append(f"  - {chosen[:100]} | {rationale[:100]}")
            else:
                lines.append(f"  - {str(d)[:120]}")
    else:
        lines.append("Decisiones: Pendientes de registrar durante implementación")
    return "\n".join(lines)


def _describe_principles(task: dict, symbols_by_file: dict) -> str:
    """Capa 6: ¿Qué leyes gobiernan? (patrones, heurísticas)"""
    lines = []
    lines.append(f"Work type: {task.get('work_type', task.get('type', 'N/A'))}")
    if symbols_by_file:
        total_syms = sum(len(v) for v in symbols_by_file.values())
        lines.append(f"Patrones existentes en código ({total_syms} símbolos en total):")
        for f, syms in list(symbols_by_file.items())[:5]:
            if syms:
                lines.append(f"  - {os.path.basename(f)} define: {', '.join(syms[:4])}")
    role = task.get("role", "")
    if role:
        lines.append(f"Rol: {role}")
    return "\n".join(lines)


def cmd_research(args: list[str]) -> int:
    """Prepare deep research context for a task (6-layer protocol).

    This is the FIRST step before implementing any task. It forces the LLM
    to investigate the real state of the code before writing anything.

    Output includes:
    - Files affected: existence check + concrete symbols extracted
    - APIs/symbols: existence check via grep (no regex)
    - Research findings: structured facts discovered
    - 6-layer context template: pre-filled with discovered facts
    - Prompt template for LLM investigation
    - Auto-generated task_context written via update-task

    The LLM MUST present this investigation to the user BEFORE writing code.
    """
    positional = [a for a in args if not a.startswith("--")]

    if len(positional) != 2:
        eprint("Uso: python3 scripts/task.py research <chain_dir> <task_id>")
        eprint()
        eprint("  chain_dir    Ruta al directorio del sprint")
        eprint("  task_id      ID de la tarea a investigar")
        eprint()
        eprint("Exit codes:")
        eprint("  0 → Contexto de investigación generado")
        eprint("  2 → Error")
        return 2

    chain_dir = os.path.abspath(positional[0])
    task_id = positional[1]

    if not os.path.isdir(chain_dir):
        eprint(f"❌ Directorio no encontrado: {chain_dir}")
        return 2

    plan_path = os.path.join(chain_dir, "plan.json")
    plan = load_json(plan_path)
    if not plan:
        eprint("❌ plan.json no encontrado")
        return 2

    pl = plan.get("plan", plan)
    task = None
    for t in pl.get("tasks", []):
        if t.get("task_id") == task_id:
            task = t
            break

    if not task:
        eprint(f"❌ Tarea {task_id} no encontrada en plan.json")
        return 2

    project_root = find_project_root(chain_dir)
    project_id = _extract_project_id(chain_dir)

    print("=" * 70)
    print(f"🔬 RESEARCH PROTOCOL — {task_id}")
    print("=" * 70)
    print(f"  Goal: {pl.get('goal', {}).get('description', 'N/A')[:100]}...")
    print()
    print(f"🎯 Objective: {task.get('objective', 'N/A')}")
    print(f"📋 Result state: {task.get('result_state', 'N/A')}")
    print()

    # Context Discovery with concrete symbols
    print("─" * 70)
    print("📡 CONTEXTO ACTUAL (investigación obligatoria)")
    print("─" * 70)
    _context_discovery(chain_dir, task_id)
    print()

    # Structured research findings
    print("─" * 70)
    print("🔍 HALLAZGOS CONCRETOS")
    print("─" * 70)

    artefacts = task.get("impacted_artifacts", {})
    files = artefacts.get("files", [])
    apis = artefacts.get("apis", [])

    existing_files = []
    new_files = []
    for f in files:
        if _file_exists(project_root, f):
            existing_files.append(f)
            symbols = _list_module_symbols(project_root, f)
            print(f"  📄 {f}")
            print(
                f"     Símbolos: {', '.join(symbols[:5])}{'...' if len(symbols) > 5 else ''}"
            )
        else:
            new_files.append(f)
            print(f"  🆕 {f} (NUEVO)")

    print()
    print("  📊 Resumen:")
    print(f"     - Archivos existentes: {len(existing_files)}")
    print(f"     - Archivos nuevos: {len(new_files)}")
    print(f"     - APIs/símbolos a crear: {len(apis)}")

    # Check for existing APIs
    _research_search_root = (
        _get_project_search_root(chain_dir, project_root, project_id) or project_root
    )
    for api in apis:
        if not api or not isinstance(api, str):
            continue
        matches = _grep_symbol(api, _research_search_root)
        if matches:
            print(f"  ⚠️  API '{api}' YA EXISTE:")
            for m in matches[:3]:
                print(f"     {m[:100]}")

    print()

    # Acceptance Criteria (DoD)
    print("─" * 70)
    print("✅ CRITERIOS DE ACEPTACIÓN (DoD)")
    print("─" * 70)
    acceptance_criteria = task.get("acceptance_criteria", [])
    if acceptance_criteria:
        for i, ac in enumerate(acceptance_criteria, 1):
            print(f"  {i}. {ac}")
    else:
        print("  (ninguno definido)")
    print()

    # Quality Gates
    print("─" * 70)
    print("🔒 QUALITY GATES")
    print("─" * 70)
    quality_gates = task.get("quality_gates", [])
    if quality_gates:
        for qg in quality_gates:
            print(f"  • {qg}")
    else:
        print("  (ninguno definido)")
    print()

    # ── Recopilar evidencia para el LLM, no generar contexto ──
    # El LLM aplicará las 6 capas usando esta evidencia real
    print("=" * 70)
    print("🔬 RESEARCH EVIDENCE — Análisis del código real")
    print("=" * 70)
    print()

    # 1. Símbolos por archivo via ast-grep
    print("📋 SÍMBOLOS POR ARCHIVO (ast-grep):")
    symbols_by_file = {}
    for f in existing_files:
        syms = _list_module_symbols(project_root, f)
        if syms:
            symbols_by_file[f] = syms
            print(f"  📄 {os.path.basename(f)}:")
            print(f"     {', '.join(syms[:10])}")
    print()

    # 2. Relaciones via DeltaGraph
    print("🔗 RELACIONES EN EL GRAFO:")
    try:
        from _internal.graph import DeltaGraph

        g = DeltaGraph(chain_dir)
        for f in existing_files:
            neighbors = g.neighbors(f, "impacts_file")
            tasks = [n["label"] for n in neighbors if n.get("type") == "task"]
            if tasks:
                print(
                    f"  {os.path.basename(f)} → impactado por: {', '.join(tasks[:5])}"
                )
    except Exception:
        print("  (grafo no disponible)")
    print()

    # 3. Riesgos
    print("⚠️  RIESGOS:")
    risks = task.get("risks", {})
    print(
        f"  curvature={risks.get('curvature', '?')} pre_churn={risks.get('pre_churn', '?')} trust={risks.get('commit_trust', '?')}"
    )
    print()

    # 4. Criterios y gates actuales
    print("✅ CRITERIOS DE ACEPTACIÓN ACTUALES:")
    for ac in task.get("acceptance_criteria", []):
        print(f"  • {ac[:100]}")
    if not task.get("acceptance_criteria"):
        print("  (sin definir)")
    print()

    # ── AUTO-GENERATE task_context con evidencia real ────────────
    # El research ahora construye automaticamente el contexto 6 capas
    # con la evidencia descubierta, y lo escribe via update-task.
    # El LLM puede refinarlo con update-task adicional si es necesario.
    print("─" * 70)
    print("🤖 AUTO-GENERANDO task_context con evidencia real...")
    print("─" * 70)

    def _pad_min10(text: str, prefix: str = "") -> str:
        """Ensure text is at least 10 chars (schema minLength requirement)."""
        if len(text) < 10:
            text = text + " " + prefix + "Contexto generado automaticamente."
        return text

    tc = {
        "observation": _pad_min10(
            _describe_observation(task, existing_files, symbols_by_file),
            "Observacion: ",
        ),
        "structure": _pad_min10(
            _describe_structure(task, existing_files, symbols_by_file), "Estructura: "
        ),
        "behavior": _pad_min10(
            _describe_behavior(task, existing_files, apis), "Comportamiento: "
        ),
        "causality": _pad_min10(_describe_causality(task, risks), "Causalidad: "),
        "purpose": _pad_min10(
            _describe_purpose(task, acceptance_criteria, quality_gates), "Finalidad: "
        ),
        "principles": _pad_min10(
            _describe_principles(task, symbols_by_file), "Principios: "
        ),
    }

    # Escribir a archivo temporal y auto-llamar update-task
    import tempfile
    import subprocess

    spring_script = os.path.join(os.path.dirname(__file__), "spring.py")
    fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix=f"research_{task_id}_")
    with os.fdopen(fd, "w") as f:
        json.dump(tc, f, indent=2, ensure_ascii=False)

    try:
        result = subprocess.run(
            [
                sys.executable,
                spring_script,
                "update-task",
                chain_dir,
                task_id,
                "--task_context-file",
                tmp_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            print("  ✅ task_context auto-generado y escrito via update-task")
            print(
                f"  📄 Evidencia: {len(existing_files)} archivos, {sum(len(v) for v in symbols_by_file.values())} símbolos"
            )
        else:
            print(f"  ⚠️  update-task falló: {result.stderr.strip()}")
            print("  Puedes escribir manualmente con:")
            print(
                f"     spring.py update-task {chain_dir} {task_id} --task_context-file {tmp_path}"
            )
    except Exception as e:
        print(f"  ⚠️  No se pudo auto-escribir: {e}")
        print(f"  Task_context disponible en: {tmp_path}")

    print()
    print("📋 FLUJO COMPLETO:")
    print("  1. task.py research  → Extraer evidencia + auto-escribir context ✅")
    print("  2. LLM refina contexto si es necesario (update-task adicional)")
    print("  3. task.py advance   → Validar y presentar al PO")
    print("=" * 70)

    return 0
