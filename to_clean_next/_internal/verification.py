#!/usr/bin/env python3
"""verification.py — Motor unificado de verificación de predicates.

Resuelve la inconsistencia entre task.py (ast-grep) y spring.py (subprocess):
Un mismo predicate se verifica igual sin importar qué script lo ejecute.

Tipos de predicate soportados:
  - String (legacy): Lenguaje natural → _parse_predicate + ast-grep
  - Dict typed:      ``{"type": "symbol_exists", "symbol": "X", "file": "Y.py"}``
                      ``{"type": "command_exit", "command": "python3 script.py"}``
                      ``{"type": "command_output", "command": "...", "contains": "text"}``

Cache de sesión:
  ``verify_predicate()`` cachea resultados en memoria (TTL 5 min).
  ``verify_predicates_batch()`` permite verificación agrupada (batch ast-grep).
  ``invalidate_verify_cache()`` limpia la cache forzadamente.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from string import Template

# Lazy imports inside function bodies to avoid circular import with advance.py

# ── Session cache ─────────────────────────────────────────────────
_verify_cache: dict[str, tuple[bool, dict, float]] = {}
CACHE_TTL = 300  # 5 minutos


def _cache_key(predicate, project_root: str, extra: str = "") -> str:
    """Genera key determinística para cache de verification.

    Args:
        predicate: String o dict del predicate.
        project_root: Ruta absoluta del proyecto.
        extra: Contexto adicional (ej: id de tarea)

    Returns:
        MD5 hex digest como key.
    """
    if isinstance(predicate, dict):
        pred_str = json.dumps(predicate, sort_keys=True)
    else:
        pred_str = str(predicate)
    raw = f"{pred_str}|{project_root}|{extra}"
    return hashlib.md5(raw.encode()).hexdigest()


def invalidate_verify_cache() -> None:
    """Limpia toda la cache de verificación."""
    _verify_cache.clear()


def _prune_cache() -> None:
    """Elimina entradas expiradas de la cache."""
    now = time.time()
    expired = [k for k, (_, _, ts) in _verify_cache.items() if now - ts > CACHE_TTL]
    for k in expired:
        del _verify_cache[k]


# ── Shell command detection ─────────────────────────────────────
_EXECUTABLE_PREFIXES = (
    "python3",
    "python",
    "bash",
    "sh",
    "zsh",
    "test",
    "[",
    "grep",
    "rg",
    "ag",
    "find",
    "cat",
    "head",
    "tail",
    "diff",
    "cmp",
    "wc",
    "ls",
    "echo",
    "git",
    "npm",
    "npx",
    "node",
    "docker",
    "make",
    "pytest",
    "which",
    "command",
)
# NOTA: <, >, <<, >> NO están en esta lista porque en el contexto
# del skill se usan como marcadores de placeholder (<chain>, <pid>)
# y no como operadores de redirección shell real.
_SHELL_OPERATORS = ("|", "&&", "||", ";", "2>&1", "`")

# Palabras que indican lenguaje natural tras un posible CLI prefix.
# Si la segunda palabra del predicate está aquí, se trata como NL.
_NATURAL_LANG_TRIGGERS = frozenset(
    {
        "muestra",
        "retorna",
        "contiene",
        "genera",
        "incluye",
        "tiene",
        "describe",
        "agrega",
        "crea",
        "verifica",
        "valida",
        "lista",
        "show",
        "returns",
        "contains",
        "generates",
        "includes",
        "adds",
        "creates",
        "verifies",
        "validates",
        "lists",
        "describes",
        "debe",
        "should",
        "must",
        "puede",
        "can",
        "cada",
        "los",
        "las",
        "una",
        "un",
        "el",
        "la",
        "al",
        "del",
        "para",
        "con",
        "sin",
        "entre",
        "despues",
        "intentar",
        "modificar",
        "después",
        "usar",
        "no",
    }
)


def _is_string_a_shell_command(predicate_text: str) -> bool:
    """Detecta si un predicate string es un comando shell ejecutable.

    Reglas:
      1. Si el primer token está en _EXECUTABLE_PREFIXES Y el segundo
         NO es lenguaje natural → shell.
      2. Si contiene operadores shell reales (|, &&, ||, ;, 2>&1, `) → shell.
      3. Si el primer token es un path absoluto (/) → shell.
      En cualquier otro caso → lenguaje natural (ast-grep).
    """
    text = predicate_text.strip()
    if not text:
        return False

    words = text.split()
    first_word = words[0] if words else ""

    # Caso 3: path absoluto → shell
    if first_word.startswith("/"):
        return True

    # Caso 1: prefix conocido
    if any(
        first_word == p or first_word.startswith(p + "/") for p in _EXECUTABLE_PREFIXES
    ):
        # Si CUALQUIER token después del primero es lenguaje natural
        # → es descripción gramatical, no comando ejecutable
        if any(w in _NATURAL_LANG_TRIGGERS for w in words[1:]):
            return False
        return True

    # Caso 2: operadores shell
    if any(op in text for op in _SHELL_OPERATORS):
        return True

    return False


# ── Main verification entry point ───────────────────────────────


def verify_predicate(predicate, project_root: str) -> tuple[bool, dict]:
    """Verifica un predicate contra código real.

    Utiliza cache de sesión: si el mismo predicate ya se verificó
    contra el mismo project_root hace menos de CACHE_TTL segundos,
    retorna el resultado cacheado. Los comandos shell NUNCA se cachean
    (pueden tener side effects).

    Args:
        predicate: String (lenguaje natural o comando shell) o dict estructurado.
        project_root: Ruta absoluta a la raíz del proyecto.

    Returns:
        (passed: bool, info: dict with method, detail, error)
    """
    # Cache check (solo para predicates no-shell)
    key = _cache_key(predicate, project_root)
    now = time.time()
    _prune_cache()

    if key in _verify_cache:
        passed, info, _ts = _verify_cache[key]
        # Devolver copia para evitar mutación externa
        return passed, dict(info)

    # ── Verificación real ──
    # ── Verificación real ──
    if isinstance(predicate, str):
        if _is_string_a_shell_command(predicate):
            passed, info = _verify_shell_string(predicate, project_root)
        else:
            # Natural language: try symbol_exists (backwards compatible) + deprecation warning
            passed, info = _verify_natural_language(predicate, project_root)
            print(
                Template('  Predicate en lenguaje natural: "$pred"').substitute(
                    pred=predicate[:60]
                )
            )
            print(
                '     Usa formato estructurado: {"type": "symbol_exists", "symbol": "...", "file": "..."}'
            )
            print("     Tipos: symbol_exists, file_exists, grep, shell, manual")
        _cache_set(key, passed, info, now)
        return passed, info

    if not isinstance(predicate, dict):
        result = (
            False,
            {
                "method": "unknown",
                "error": f"tipo inválido: {type(predicate).__name__}",
                "label": f"tipo inválido: {type(predicate).__name__}",
            },
        )
        return result

    ptype = predicate.get("type", "")
    if ptype == "symbol_exists":
        passed, info = _verify_symbol_exists(predicate, project_root)
    elif ptype == "symbol_not_exists":
        passed, info = _verify_symbol_not_exists(predicate, project_root)
    elif ptype == "command_exit":
        passed, info = _verify_command_exit(predicate, project_root)
    elif ptype == "command_output":
        passed, info = _verify_command_output(predicate, project_root)
    elif ptype == "test_pass":
        passed, info = _verify_test_pass(predicate, project_root)
    elif ptype == "file_exists":
        path = predicate.get("path", "")
        passed = os.path.exists(os.path.join(project_root, path)) if path else False
        info = {"method": "file_exists", "path": path, "label": f"Archivo: {path}"}
    elif ptype == "grep":
        pattern = predicate.get("pattern", "")
        inc = predicate.get("include", "*")
        import subprocess

        r = subprocess.run(
            ["grep", "-rl", pattern, "--include=" + inc, project_root],
            capture_output=True,
            text=True,
            timeout=10,
        )
        passed = r.returncode == 0 and r.stdout.strip() != ""
        info = {
            "method": "grep",
            "pattern": pattern,
            "files": r.stdout[:200] if passed else "",
            "label": f"grep: {pattern}",
        }
    elif ptype == "manual":
        desc = predicate.get("description", predicate.get("label", ""))
        # manual: siempre pasa, solo documenta el criterio
        passed = True
        info = {
            "method": "manual",
            "description": desc,
            "note": "Verificacion manual requerida",
            "label": f"[manual] {desc[:80]}",
        }
    else:
        return False, {
            "method": "unknown",
            "type": ptype,
            "error": f"tipo de predicate desconocido: {ptype}",
            "label": f"tipo desconocido: {ptype}",
        }

    # Cachear resultado (todos los predicates son idempotentes)
    _cache_set(key, passed, info, now)
    return passed, info


def _cache_set(key: str, passed: bool, info: dict, now: float) -> None:
    """Guarda en cache solo si no hubo error."""
    if not info.get("error"):
        _verify_cache[key] = (passed, info, now)


# ── Batch verification ─────────────────────────────────────────


def verify_predicates_batch(
    predicates: list,
    project_root: str,
) -> list[tuple[bool, dict]]:
    """Verifica múltiples predicates en batch con optimización.

    Para predicates del tipo ``symbol_exists``, agrupa los símbolos
    por lenguaje y archivo, y ejecuta ``sg`` (ast-grep) una sola vez
    por grupo en lugar de un proceso por predicate.

    Para otros tipos, delega en ``verify_predicate()`` individual
    (que ya usa cache).

    Args:
        predicates: Lista de predicates (strings o dicts).
        project_root: Ruta absoluta del proyecto.

    Returns:
        Lista de (passed, info) en el mismo orden que la entrada.
    """
    _prune_cache()

    # Separar predicates por tipo
    symbol_indices: list[int] = []  # índices de symbol_exists
    symbol_preds: list[dict] = []
    other: list[tuple[int, object]] = []  # (índice, predicate)

    for idx, p in enumerate(predicates):
        key = _cache_key(p, project_root)
        if key in _verify_cache:
            continue  # Ya cacheado, se resuelve abajo

        # Detectar symbol_exists (dict o string natural language)
        if isinstance(p, dict) and p.get("type") == "symbol_exists":
            symbol_indices.append(idx)
            symbol_preds.append(p)
        elif isinstance(p, str) and not _is_string_a_shell_command(p):
            # Natural language string → tratar como symbol_exists
            symbol_indices.append(idx)
            symbol_preds.append({"type": "symbol_exists", "symbol": p, "_original": p})
        else:
            other.append((idx, p))

    # Resolver batch ast-grep
    if symbol_preds:
        _batch_verify_symbols(symbol_indices, symbol_preds, project_root, predicates)

    # Resolver el resto (individual, ya cacheados o comandos shell)
    results: dict[int, tuple[bool, dict]] = {}
    for idx, p in other:
        results[idx] = verify_predicate(p, project_root)

    # Completar con cache
    for idx, p in enumerate(predicates):
        if idx not in results:
            key = _cache_key(p, project_root)
            if key in _verify_cache:
                passed, info, _ = _verify_cache[key]
                results[idx] = (passed, dict(info))
            else:
                # No cacheado y no procesado → verificar individual
                results[idx] = verify_predicate(p, project_root)

    # Devolver en orden
    return [results[i] for i in range(len(predicates))]


def _batch_verify_symbols(
    indices: list[int],
    symbol_preds: list[dict],
    project_root: str,
    all_predicates: list,
) -> None:
    """Ejecuta ast-grep batch para múltiples symbol_exists predicates.

    Estrategia:
      1. Agrupa símbolos por ``file`` hint resuelto (si falla, individual).
      2. Ejecuta ``sg`` UNA VEZ por archivo con todos los símbolos que
         pertenecen a ese archivo.
      3. Los símbolos SIN file_hint o con hint no resoluble se verifican
         individualmente (delegan en _verify_symbol_exists que maneja
         grep fallback correctamente).

    Esto elimina el problema de ``_collect_ast_files`` que excluye
    directorios ``.`` (como ``.claude/``).
    """
    from _internal.advance import _AST_GREP_AVAILABLE, _AST_LANG_MAP, _AST_DEF_PATTERNS

    time.time()

    # ── 1. Separar los que tienen file_hint resoluble ──
    batched: list[tuple[int, str, str, dict]] = []  # (idx, resolved_path, symbol, pred)
    individual: list[int] = []

    for idx, p in zip(indices, symbol_preds):
        symbol = p.get("symbol", "")
        orig = p.get("_original", "")
        if not symbol and orig:
            symbol = orig
        if not symbol:
            continue

        file_hint = p.get("file", "")
        if file_hint:
            resolved = _resolve_target(file_hint, project_root)
            if resolved != project_root and os.path.isfile(resolved):
                batched.append((idx, resolved, symbol, p))
                continue

        # Sin file_hint resoluble → individual
        individual.append(idx)

    # ── 2. Batch: agrupar por archivo, ejecutar sg una vez por archivo ──
    by_file: dict[str, list[tuple[int, str, dict]]] = {}
    for idx, fpath, symbol, p in batched:
        by_file.setdefault(fpath, []).append((idx, symbol, p))

    for fpath, items in by_file.items():
        ext = os.path.splitext(fpath)[1].lower()
        lang = _AST_LANG_MAP.get(ext, "python")
        patterns = _AST_DEF_PATTERNS.get(lang, ["def {name}($$$)", "class {name}"])

        symbols_in_file = [s for _, s, _ in items]
        symbol_found: dict[str, bool] = {s: False for s in symbols_in_file}

        for tmpl in patterns:
            if all(symbol_found.values()):
                break
            remaining = [s for s in symbols_in_file if not symbol_found[s]]
            if not remaining:
                continue

            if _AST_GREP_AVAILABLE:
                sg_args = ["sg", "-l", lang, "--json"]
                for s in remaining:
                    sg_args.extend(["-p", tmpl.format(name=s)])
                sg_args.append(fpath)

                try:
                    result = subprocess.run(
                        sg_args, capture_output=True, text=True, timeout=10
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        try:
                            matches = json.loads(result.stdout)
                            if isinstance(matches, list):
                                for match in matches:
                                    match_text = match.get("text", "")
                                    for s in remaining:
                                        def_pat = f"def {s}("
                                        class_pat = f"class {s}"
                                        if not symbol_found.get(s, False) and (
                                            def_pat in match_text
                                            or class_pat in match_text
                                        ):
                                            symbol_found[s] = True
                        except json.JSONDecodeError:
                            pass
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass

            # Fallback grep directo para símbolos no encontrados
            for s in remaining:
                if not symbol_found.get(s, False):
                    try:
                        r = subprocess.run(
                            ["grep", "-qn", f"def {s}(", fpath],
                            capture_output=True,
                            timeout=5,
                        )
                        if r.returncode == 0:
                            symbol_found[s] = True
                        else:
                            r2 = subprocess.run(
                                ["grep", "-qn", f"class {s}", fpath],
                                capture_output=True,
                                timeout=5,
                            )
                            if r2.returncode == 0:
                                symbol_found[s] = True
                    except (subprocess.TimeoutExpired, FileNotFoundError):
                        pass

        # Cachear resultados de este archivo
        for idx, s, p in items:
            passed = symbol_found.get(s, False)
            label = f"{s} en {os.path.basename(fpath)}"
            info = {
                "method": "ast-grep",
                "symbol": s,
                "file": fpath,
                "found": passed,
                "label": label,
            }
            key = _cache_key(all_predicates[idx], project_root)
            _verify_cache[key] = (passed, info, time.time())

    # ── 3. Fallback individual (sin file_hint resoluble) ──
    for idx in individual:
        # Encontrar el predicate original en symbol_preds
        try:
            pos = indices.index(idx)
            orig_pred = symbol_preds[pos]
            # Si tiene _original, es string natural language → usar verify_predicate
            orig_raw = orig_pred.get("_original", "")
            if orig_raw:
                passed, info = verify_predicate(orig_raw, project_root)
            else:
                passed, info = verify_predicate(orig_pred, project_root)
        except ValueError:
            passed, info = (
                False,
                {"method": "error", "error": "idx not found in batch", "label": "?"},
            )
        key = _cache_key(all_predicates[idx], project_root)
        _cache_set(key, passed, info, time.time())


def _resolve_target(file_hint: str, project_root: str) -> str:
    """Resuelve file_hint a ruta absoluta.

    Pruebas (en orden):
      1. file_hint relativo a project_root
      2. file_hint como ruta absoluta directa

    Retorna project_root si no resuelve (causa fallback a individual).
    """
    if not file_hint:
        return project_root
    candidate = os.path.join(project_root, file_hint)
    if os.path.exists(candidate):
        return os.path.abspath(candidate)
    if os.path.exists(file_hint):
        return os.path.abspath(file_hint)
    return project_root


# ── String handlers ─────────────────────────────────────────────


def _verify_natural_language(text: str, project_root: str) -> tuple[bool, dict]:
    """Verifica predicate de lenguaje natural con ast-grep."""
    from _internal.advance import _parse_predicate, run_verification

    try:
        pattern, vtype, fhint = _parse_predicate(text)
    except Exception as exc:
        return False, {
            "method": "ast-grep",
            "error": f"no se pudo parsear: {exc}",
            "label": text[:70],
        }

    check_type = vtype if vtype in ("must_exist", "must_not_exist") else "must_exist"
    try:
        passed = run_verification(
            {"predicate": pattern, "type": check_type},
            project_root,
            file_hint=fhint,
        )
    except Exception as exc:
        return False, {
            "method": "ast-grep",
            "error": f"error en verificación: {exc}",
            "label": text[:70],
        }

    label = text[:70]
    return passed, {
        "method": "ast-grep",
        "pattern": pattern,
        "type": check_type,
        "file_hint": fhint,
        "label": label,
    }


def _verify_shell_string(text: str, project_root: str) -> tuple[bool, dict]:
    """Ejecuta predicate string como comando shell."""
    try:
        result = subprocess.run(
            text,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=project_root,
        )
        passed = result.returncode == 0
        info = {
            "method": "shell",
            "command": text[:80],
            "exit_code": result.returncode,
            "stdout": result.stdout[:200] if result.stdout else "",
            "stderr": result.stderr[:200] if result.stderr else "",
            "label": text[:70],
        }
        return passed, info
    except subprocess.TimeoutExpired:
        return False, {
            "method": "shell",
            "command": text[:80],
            "error": "timeout (30s)",
            "label": text[:70],
        }
    except Exception as exc:
        return False, {
            "method": "shell",
            "command": text[:80],
            "error": str(exc),
            "label": text[:70],
        }


# ── Structured handlers ─────────────────────────────────────────


def _verify_symbol_exists(predicate: dict, project_root: str) -> tuple[bool, dict]:
    """symbol_exists: verifica que un símbolo exista en un archivo (ast-grep)."""
    from _internal.advance import run_verification

    symbol = predicate.get("symbol", "")
    file_hint = predicate.get("file")

    if not symbol:
        return False, {
            "method": "symbol_exists",
            "error": "campo 'symbol' requerido",
            "label": "symbol_exists: campo 'symbol' requerido",
        }

    try:
        passed = run_verification(
            {"predicate": symbol, "type": "must_exist"},
            project_root,
            file_hint=file_hint,
        )
    except Exception as exc:
        label = f"{symbol} en {file_hint}" if file_hint else symbol
        return False, {
            "method": "symbol_exists",
            "symbol": symbol,
            "file": file_hint,
            "error": str(exc),
            "label": label,
        }

    label = f"{symbol} en {file_hint}" if file_hint else symbol
    return passed, {
        "method": "symbol_exists",
        "symbol": symbol,
        "file": file_hint,
        "label": label,
    }


def _verify_symbol_not_exists(predicate: dict, project_root: str) -> tuple[bool, dict]:
    """symbol_not_exists: verifica que un símbolo NO exista (ast-grep)."""
    from _internal.advance import run_verification

    symbol = predicate.get("symbol", "")
    file_hint = predicate.get("file")

    if not symbol:
        return False, {
            "method": "symbol_not_exists",
            "error": "campo 'symbol' requerido",
            "label": "symbol_not_exists: campo 'symbol' requerido",
        }

    try:
        passed = not run_verification(
            {"predicate": symbol, "type": "must_not_exist"},
            project_root,
            file_hint=file_hint,
        )
    except Exception as exc:
        label = f"NO {symbol} en {file_hint}" if file_hint else f"NO {symbol}"
        return False, {
            "method": "symbol_not_exists",
            "symbol": symbol,
            "file": file_hint,
            "error": str(exc),
            "label": label,
        }

    label = f"NO {symbol} en {file_hint}" if file_hint else f"NO {symbol}"
    return passed, {
        "method": "symbol_not_exists",
        "symbol": symbol,
        "file": file_hint,
        "label": label,
    }


def _verify_command_exit(predicate: dict, project_root: str) -> tuple[bool, dict]:
    """command_exit: ejecuta un comando y verifica exit code 0."""
    command = predicate.get("command", "")
    if not command:
        return False, {"method": "command_exit", "error": "campo 'command' requerido"}

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=project_root,
        )
        passed = result.returncode == 0
        return passed, {
            "method": "command_exit",
            "command": command[:80],
            "exit_code": result.returncode,
            "label": command[:70],
        }
    except subprocess.TimeoutExpired:
        return False, {
            "method": "command_exit",
            "command": command[:80],
            "error": "timeout (30s)",
            "label": command[:70],
        }
    except Exception as exc:
        return False, {
            "method": "command_exit",
            "command": command[:80],
            "error": str(exc),
            "label": command[:70],
        }


def _verify_command_output(predicate: dict, project_root: str) -> tuple[bool, dict]:
    """command_output: ejecuta un comando y verifica que contenga texto."""
    command = predicate.get("command", "")
    contains = predicate.get("contains", "")

    if not command:
        return False, {
            "method": "command_output",
            "error": "campo 'command' requerido",
            "label": "command_output: campo 'command' requerido",
        }
    if not contains:
        return False, {
            "method": "command_output",
            "error": "campo 'contains' requerido",
            "label": "command_output: campo 'contains' requerido",
        }

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=project_root,
        )
        combined = (result.stdout or "") + (result.stderr or "")
        found = contains in combined
        label = (
            f"{command[:50]} (contiene: {contains[:30]})" if contains else command[:70]
        )
        return found, {
            "method": "command_output",
            "command": command[:80],
            "contains": contains,
            "found": found,
            "exit_code": result.returncode,
            "label": label,
        }
    except subprocess.TimeoutExpired:
        label = (
            f"{command[:50]} (contiene: {contains[:30]})" if contains else command[:70]
        )
        return False, {
            "method": "command_output",
            "command": command[:80],
            "contains": contains,
            "error": "timeout",
            "label": label,
        }
    except Exception as exc:
        label = (
            f"{command[:50]} (contiene: {contains[:30]})" if contains else command[:70]
        )
        return False, {
            "method": "command_output",
            "command": command[:80],
            "contains": contains,
            "error": str(exc),
            "label": label,
        }


def _verify_test_pass(predicate: dict, project_root: str) -> tuple[bool, dict]:
    """test_pass: ejecuta pytest en un archivo y verifica que pase."""
    command = predicate.get("command", "")
    if not command:
        return False, {
            "method": "test_pass",
            "error": "campo 'command' requerido (ej: pytest tests/test_foo.py)",
            "label": "test_pass: campo 'command' requerido",
        }

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=project_root,
        )
        passed = result.returncode == 0
        return passed, {
            "method": "test_pass",
            "command": command[:80],
            "exit_code": result.returncode,
            "label": command[:70],
        }
    except subprocess.TimeoutExpired:
        return False, {
            "method": "test_pass",
            "command": command[:80],
            "error": "timeout (60s)",
            "label": command[:70],
        }
    except Exception as exc:
        return False, {
            "method": "test_pass",
            "command": command[:80],
            "error": str(exc),
            "label": command[:70],
        }
