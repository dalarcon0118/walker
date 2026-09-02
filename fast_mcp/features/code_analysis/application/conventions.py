"""
application/conventions.py — CheckConventionsUseCase.

Chequea convenciones del proyecto (almacenadas en el CKG) contra el código
real (via ast-grep). Las convenciones son KnowledgeFacts tipo "convention"
en el CKG (SSOT de conocimiento), con la regla ast-grep YAML en metadata.

Flujo:
  1. kg_query(type="convention")           → reglas activas del CKG
  2. escribe cada rule_yaml a un temp file
  3. ast-grep scan --rule <file> <repo>    → ejecuta contra el código
  4. parsea el JSON de matches
  5. reporte: {rule, file, line, text, severity, fix}

Sin librerías nuevas: ast-grep ya es dependencia del walker.
Return pattern: execute() devuelve (report, errors) — nunca lanza
(convention-001: cero except silenciosos).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile

logger = logging.getLogger(__name__)


def execute(
    chain_dir: str,
    languages: list[str] | None = None,
    ckg: Any | None = None,
) -> tuple[dict, list[str]]:
    """Chequea las convenciones del CKG contra el código.

    Args:
        chain_dir: Directorio del sprint (para resolver el repo y el CKG).
        languages: Filtro opcional de lenguajes (ej: ["Python", "TypeScript"]).
        ckg: CKG ya construido (inyectado por el composition root / handler).
            Regla de coherencia §3bis R1: este feature NO importa
            cognitive_knowledge; recibe el servicio cableado externamente.

    Returns:
        (report, errors): report = {rules_checked, violations, by_rule}
    """
    errors: list[str] = []

    # 1. Resolver repo real (donde vive el código) y data_dir (donde vive el CKG)
    try:
        from fast_mcp.shared_kernel.project_support import ProjectService

        paths = ProjectService.resolve_paths(chain_dir)
        repo_root = paths.repo_path or paths.project_root
        data_dir = paths.data_dir
    except Exception as e:
        return {"rules_checked": 0, "violations": [], "by_rule": {}}, [str(e)]

    if not repo_root or not os.path.isdir(repo_root):
        return {"rules_checked": 0, "violations": [], "by_rule": {}}, [
            f"repo no resuelto: {repo_root}"
        ]

    # 2. Cargar convenciones del CKG (inyectado — nunca importado aquí)
    rules, rule_errors = _load_conventions(data_dir, languages, ckg)
    errors.extend(rule_errors)
    if not rules:
        return {"rules_checked": 0, "violations": [], "by_rule": {}}, errors

    # 3. Ejecutar cada regla contra el repo
    violations: list[dict] = []
    by_rule: dict[str, list[dict]] = {}
    for rule in rules:
        hits, hit_errors = _run_rule(rule, repo_root)
        errors.extend(hit_errors)
        if hits:
            by_rule[rule.get("rule_id", "?")] = hits
            violations.extend(hits)

    report = {
        "rules_checked": len(rules),
        "violations": violations,
        "by_rule": by_rule,
        "repo": repo_root,
    }
    return report, errors


# ── Internals ──

def _load_conventions(
    data_dir: str | None,
    languages: list[str] | None,
    ckg: Any | None = None,
) -> tuple[list[dict], list[str]]:
    """Carga convenciones activas del CKG (tipo convention).

    El CKG se recibe INYECTADO (regla §3bis R1): este módulo no importa
    cognitive_knowledge. Si no llega, devuelve error explícito (nunca
    simula un chequeo vacío).
    """
    errors: list[str] = []
    if not data_dir:
        return [], []
    if ckg is None:
        return [], ["CKG no inyectado — conventions requiere el ckg del composition root"]

    try:
        nodes = ckg.query({"type": "convention"}) or []
    except Exception as e:
        return [], [f"CKG query failed: {e}"]

    rules = []
    for src in nodes:
        meta = src.get("metadata", {}) or {}
        rule_yaml = meta.get("rule_yaml", "")
        if not rule_yaml:
            continue
        rule_lang = meta.get("language", "") or src.get("scope", "")
        if languages and rule_lang and rule_lang not in languages:
            continue
        rules.append({
            "rule_id": meta.get("rule_id", src.get("statement", "?")[:30]),
            "statement": src.get("statement", ""),
            "language": rule_lang,
            "severity": meta.get("severity", "warning"),
            "fix": meta.get("fix", ""),
            "rule_yaml": rule_yaml,
        })
    return rules, errors


def _run_rule(rule: dict, repo_root: str) -> tuple[list[dict], list[str]]:
    """Ejecuta una regla ast-grep contra el repo. Returns (hits, errors)."""
    errors: list[str] = []
    rule_yaml = rule.get("rule_yaml", "")
    if not rule_yaml:
        return [], ["rule sin rule_yaml"]

    # Escribir la regla a un temp file
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".yml")
        os.write(fd, rule_yaml.encode("utf-8"))
        os.close(fd)
    except OSError as e:
        return [], [f"no se pudo escribir rule temp: {e}"]

    try:
        proc = subprocess.run(
            ["ast-grep", "scan", "--rule", tmp_path, repo_root, "--json"],
            capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError:
        return [], ["ast-grep no instalado"]
    except subprocess.TimeoutExpired:
        return [], [f"timeout en regla {rule.get('rule_id')}"]
    finally:
        try:
            os.remove(tmp_path)
        except OSError as e:
            logger.warning(
                "No se pudo limpiar rule temp %s (%s: %s)",
                tmp_path, type(e).__name__, e,
            )

    if proc.returncode != 0:
        return [], [f"ast-grep exit {proc.returncode}: {proc.stderr[:200]}"]

    try:
        matches = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as e:
        return [], [f"JSON de ast-grep inválido: {e}"]

    hits = []
    for m in matches:
        hits.append({
            "rule": rule.get("rule_id"),
            "severity": rule.get("severity"),
            "fix": rule.get("fix"),
            "file": m.get("file", ""),
            "line": (m.get("range", {}).get("start", {}) or {}).get("line", 0) + 1,
            "text": (m.get("text", "") or "").strip()[:200],
        })
    return hits, errors
