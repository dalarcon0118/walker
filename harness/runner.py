#!/usr/bin/env python3
"""Mini harness: corre tareas de evaluación sobre un repo Django con OpenCode.

Qué hace, por cada tarea y por cada repetición k:
  1. crea un git worktree en el commit base (el padre del fix),
  2. ejecuta `opencode run --format json` dentro del worktree,
  3. gradea con la suite real (`manage.py test <label>`),
  4. registra pass/fail, duración, tokens y costo en results.csv.

Qué NO hace: no implementa un loop de agente. El harness es OpenCode; esto es el
runner que lo lanza N veces y decide si pasó. Sin dependencias fuera de stdlib.

Uso:
    python scripts/eval/runner.py --tasks scripts/eval/tasks.example.json --k 2
    python scripts/eval/runner.py --tasks t.json --dry-run
    python scripts/eval/runner.py --tasks t.json --filter incident --keep

Costo: cada corrida consume tokens reales. Empezá con --filter y k=1.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import os
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SESSION_RE = re.compile(r"ses_[A-Za-z0-9]+")
DEFAULT_TIMEOUT = 900
SUMMARY_KEEP_LINES = 40
MCP_SERVERS = ("walker", "opencode-vision", "CodeGraphContext", "graphify", "memora")


def run(cmd: list[str], cwd: Path, env: dict[str, str], timeout: int) -> tuple[int, str, float]:
    started = time.time()
    # OpenCode resuelve la ubicación con $PWD: sin esto la sesión apunta al repo
    # desde donde se lanzó el runner, no al directorio de trabajo real.
    child_env = dict(env)
    child_env["PWD"] = str(cwd)
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), env=child_env, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        return proc.returncode, proc.stdout or "", time.time() - started
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        return 124, out + f"\n[timeout {timeout}s]", time.time() - started


def model_label(raw: object) -> str | None:
    """Normaliza el `model` de la sesión de OpenCode a "providerID/modelID".

    La columna guarda un JSON ({"id": ..., "providerID": ...}) o nada. Sin
    normalizar, el CSV solo podía mentir de dos formas: "(config)" cuando no se
    pasó --model, o el pin aunque la sesión hubiera corrido con otro. Medido
    2026-11-16: baseline pinned a deepseek-v4.1-flash vs rerun con el default
    de la config → tokens 1,9x que parecían un efecto del código.
    """
    if not raw:
        return None
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return raw or None
    if isinstance(data, dict):
        model_id = data.get("id") or data.get("modelID")
        provider = data.get("providerID")
        if model_id and provider:
            return f"{provider}/{model_id}"
        return str(model_id or provider or "") or None
    return None


def agent_default_model(agent: str, agents_dir: Path | None = None) -> str | None:
    """Modelo que OpenCode usará si no se pineó: el del frontmatter del agente.

    Sin esto el CSV no podía explicar nada: `--agent build` toma el modelo de
    `~/.config/opencode/agents/build.md` (hoy `opencode/beam-free`), no el
    default global. Medido 2026-11-16: baseline pinned a deepseek-v4.1-flash y
    rerun corrió beam-free → tokens_in 1,9x que parecían efecto del código.
    """
    base = agents_dir or (Path.home() / ".config/opencode/agents")
    for suffix in (".md", ".json", ".jsonc", ""):
        path = base / f"{agent}{suffix}"
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if suffix in (".json", ".jsonc"):
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("model"):
                return str(data["model"])
            continue
        # Frontmatter YAML: primer bloque --- ... ---
        match = re.search(r"^---\s*$(.+?)^---\s*$", text, re.MULTILINE | re.DOTALL)
        block = match.group(1) if match else ""
        found = re.search(r"^model:\s*[\"']?([^\s\"']+)", block, re.MULTILINE)
        if found:
            return found.group(1)
    return None


def session_usage(session_id: str | None) -> dict:
    """Lee tokens/costo/modelo de la sesión en la DB de OpenCode (best-effort)."""
    empty = {"tokens_in": None, "tokens_out": None, "cost_usd": None, "model": None}
    if not session_id:
        return empty
    db = Path.home() / ".local/share/opencode/opencode.db"
    if not db.exists():
        return empty
    for table in ("session_v2", "session"):
        try:
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
                row = conn.execute(
                    f"SELECT tokens_input, tokens_output, cost, model FROM {table} WHERE id = ?",
                    (session_id,),
                ).fetchone()
            if row:
                return {
                    "tokens_in": row[0],
                    "tokens_out": row[1],
                    "cost_usd": row[2],
                    "model": model_label(row[3]),
                }
        except sqlite3.Error:
            # La tabla `session` (v1) no tiene la misma forma que session_v2.
            continue
    return empty


def drop_worktree(repo: Path, wt: Path, env: dict[str, str]) -> None:
    shutil.rmtree(wt, ignore_errors=True)


def materialize(repo: Path, base: str, dest: Path, env: dict[str, str]) -> tuple[bool, str]:
    """Repo sintético en `dest` con UN commit en `base`: sin historia futura."""
    dest.mkdir(parents=True, exist_ok=True)
    arch = subprocess.run(["git", "archive", "--format=tar", base], cwd=str(repo),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if arch.returncode != 0:
        return False, arch.stderr.decode("utf-8", "replace")
    tar = subprocess.run(["tar", "-x", "-C", str(dest)], input=arch.stdout,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if tar.returncode != 0:
        return False, tar.stderr.decode("utf-8", "replace")
    return True, ""


def sanitize_source(src: str) -> str:
    """Quita docstrings y comentarios de línea completa.

    Un grader que narra la causa ("antes de X el shim vivía en Y") le entrega la
    respuesta al agente. Las aserciones quedan intactas; solo se borra la prosa.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    drop: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if not body:
                continue
            first = body[0]
            value = getattr(first, "value", None)
            if isinstance(first, ast.Expr) and isinstance(value, ast.Constant) and isinstance(value.value, str):
                drop.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    kept = [line for i, line in enumerate(src.splitlines(), 1)
            if i not in drop and not line.lstrip().startswith("#")]
    return "\n".join(kept) + "\n"


def inject_tests(repo: Path, task: dict, dest: Path, env: dict[str, str], sanitize: bool) -> str | None:
    """Trae los archivos de test desde `test_from` (sin el código del fix)."""
    for path in task.get("test_files") or []:
        rc, content, _ = run(["git", "show", f"{task['test_from']}:{path}"], repo, env, 60)
        if rc != 0:
            return f"no se pudo leer {path} de {task['test_from']}"
        if sanitize and task.get("sanitize") is not False:
            content = sanitize_source(content)
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return None


def _seed_knowledge(source_repo: Path, sandbox_wt: Path, env: dict[str, str]) -> int:
    """Copia el conocimiento (índice `ckg`) del proyecto real al sandbox.

    El sandbox es un `git archive` + `git init`: no tiene `.cognitive`, así
    que `prepare` (llamado antes) crea un proyecto vacío y la sección
    CONOCIMIENTO DEL PROYECTO del prompt llega en blanco — el agente nunca
    ve las convenciones y no se puede medir el efecto de inyectarlas.

    Solo se copia el `ckg` (conocimiento curado): el CPG es derivado del
    código y el del proyecto real apunta a rutas que no existen en el
    sandbox; los delta/traces son específicos del sprint. Se usa la API de
    walker (ckg_service), no SQL directo, y se dropea el FTS para que se
    re-backfillee al reabrir (evita drift FS-1).

    Devuelve la cantidad de docs copiados (0 = nada que copiar / fallo).
    """
    code = f'''
import sys
sys.path.insert(0, {str(source_repo.parent.parent if False else "/Users/davidmartinez/develop/Active-projects/walker")!r})
import os
from fast_mcp.features.cognitive_knowledge import ckg_service
from fast_mcp.shared_kernel.project_support import ProjectService

src_dir = str(ProjectService.resolve_paths({str(source_repo)!r}).data_dir)
dst_dir = str(ProjectService.resolve_paths({str(sandbox_wt)!r}).data_dir)
src = ckg_service(src_dir)
dst = ckg_service(dst_dir)
if src is None or dst is None:
    print("0"); raise SystemExit(0)

src_docs = src.query({{}}) or []
if not src_docs:
    print("0"); raise SystemExit(0)

# ckg_service.query devuelve docs sin _id: se re-keyan por el id del repo fuente
# re-leyendo la tabla de storage. Se usa el grafo subyacente para leer los ids.
src_graph = src._repo._cg
dst_graph = dst._repo._cg
docs = src_graph.get_docs("ckg") if hasattr(src_graph, "get_docs") else {{}}
if not docs:
    print("0"); raise SystemExit(0)

dst_graph.store_many("ckg", docs)
# FTS derivado: dropear para que ensure_fts lo re-backfillee al reabrir.
try:
    dst_graph._db.execute("DROP TABLE IF EXISTS ckg_fts")  # noqa: SLF001
    dst_graph._db.execute("DELETE FROM meta WHERE `index`='ckg' AND key='fts_marker'")
    dst_graph._db.commit()
except Exception as e:
    print("fts warn:", e, file=sys.stderr)
print(len(docs))
'''
    import subprocess
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        print(f"  ! seed_knowledge: {proc.stderr.strip()[:200]}", file=sys.stderr)
        return 0
    try:
        return int(proc.stdout.strip().split()[-1])
    except ValueError:
        return 0
        rc, content, _ = run(["git", "show", f"{task['test_from']}:{path}"], repo, env, 60)
        if rc != 0:
            return f"no se pudo leer {path} de {task['test_from']}"
        if sanitize and task.get("sanitize") is not False:
            content = sanitize_source(content)
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return None


def resolve_base(repo: Path, task: dict) -> str | None:
    if task.get("base_commit"):
        return str(task["base_commit"])
    fix = task.get("fix_commit")
    if not fix:
        return None
    rc, out, _ = run(["git", "rev-parse", "--verify", f"{fix}^"], repo, os.environ.copy(), 30)
    return out.strip() if rc == 0 else None


def _stats(values: list) -> dict:
    """total/mediana/min/max de una métrica, ignorando None (DB sin sesión)."""
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return {"total": None, "median": None, "min": None, "max": None}
    return {
        "total": round(sum(clean), 1),
        "median": round(statistics.median(clean), 1),
        "min": round(min(clean), 1),
        "max": round(max(clean), 1),
    }


def summarize(rows: list[dict], stamp: str, wall_s: float) -> dict:
    """Agregado por corrida: la señal está en total/mediana, NO por tarea.

    Medido 2026-11-16 con el modelo pinneado: la misma tarea contra el mismo
    commit base varió 0,71x–2,20x en tokens_in entre dos corridas. Leer una
    fila aislada del CSV lleva a conclusiones falsas sobre el código.
    """
    models = sorted({str(r.get("model")) for r in rows if r.get("model")})
    gates = [r.get("walker_gate") for r in rows]
    return {
        "run": stamp,
        "tasks": len(rows),
        "pass": sum(int(r["pass"]) for r in rows),
        "gate": sum(1 for g in gates if g == 1),
        # None = no se pidió gate (--walker-gate ausente); no es un fallo.
        "gate_requested": any(g is not None for g in gates),
        "models": models,
        "model_comparable": len(models) <= 1,
        "wall_s": round(wall_s, 1),
        "tokens_in": _stats([r.get("tokens_in") for r in rows]),
        "tokens_out": _stats([r.get("tokens_out") for r in rows]),
        "duration_s": _stats([r.get("duration_s") for r in rows]),
    }


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def detect_project(repo: Path) -> dict | None:
    """Resuelve {project_id, repo_path} del repo vía el registro de walker.

    Git-like: manifest del layout cognitivo más cercano o, si no, el registro
    global comparando `repo_path`. La capacidad existe en
    `ProjectService.resolve_from_registry`; el harness no la usaba, así que
    había que pasar `--walker-project` a mano (y sin flag no se publicaba nada,
    en silencio).

    Se corre en subproceso para no atar el harness al venv/imports de walker.
    """
    walker_root = Path(__file__).resolve().parent.parent
    snippet = (
        "import json;"
        "from fast_mcp.shared_kernel.project_support import ProjectService;"
        f"print(json.dumps(ProjectService.resolve_from_registry({str(repo)!r})))"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", snippet],
            cwd=str(walker_root),
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "PYTHONPATH": str(walker_root)},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def eval_knowledge_nodes(rows: list[dict], stamp: str) -> list[dict]:
    """Nodos de conocimiento con el ÚLTIMO resultado de cada tarea.

    Antes el harness publicaba solo al TraceGraph, así que el CKG del proyecto
    no sabía nada de las corridas: `documenting_knowledge` no se llamaba nunca.
    El id es determinístico (`eval-<task>-k<k>`) → re-correr actualiza en vez de
    duplicar; el historial completo sigue viviendo en trace (`eval.result`).
    """
    nodes: list[dict] = []
    for row in rows:
        passed = bool(row.get("pass"))
        gate = row.get("walker_gate")
        gate_txt = "n/a" if gate is None else ("ok" if gate else "fail")
        nodes.append(
            {
                "id": f"eval-{_slug(row['task'])}-k{row['k']}",
                "label": (
                    f"Eval {row['task']} k{row['k']}: "
                    f"{'PASS' if passed else 'FAIL'} — gate {gate_txt}"
                ),
                "description": (
                    f"Última corrida del harness ({stamp}) sobre {row['task']}. "
                    f"base={str(row.get('base'))[:8]} · modelo={row.get('model') or '?'} · "
                    f"duración={row.get('duration_s')}s · tokens_in={row.get('tokens_in')} · "
                    f"test_rc={row.get('test_rc')}. Cambió respecto de la corrida anterior "
                    f"si este statement cambió (id determinístico)."
                ),
                "type": "verification",
                "tags": ["verification", "eval",
                         "result:pass" if passed else "result:fail"],
                "metadata": {
                    "run": stamp,
                    "task": row["task"],
                    "k": row["k"],
                    "base": row.get("base"),
                    "model": row.get("model"),
                    "duration_s": row.get("duration_s"),
                    "tokens_in": row.get("tokens_in"),
                    "tokens_out": row.get("tokens_out"),
                    "test_rc": row.get("test_rc"),
                    "walker_gate": gate,
                    "source": "harness",
                },
            }
        )
    if rows:
        ok = sum(1 for r in rows if r.get("pass"))
        nodes.append(
            {
                "id": "eval-suite-latest",
                "label": f"Suite de eval: {ok}/{len(rows)} pass (run {stamp})",
                "description": (
                    "Estado de la última corrida completa del harness. "
                    f"{len(rows)} resultado(s) en {len({r['task'] for r in rows})} tarea(s); "
                    f"modelos={sorted({r.get('model') for r in rows if r.get('model')})}."
                ),
                "type": "verification",
                "tags": ["verification", "eval", "suite"],
                "metadata": {"run": stamp, "tasks": len(rows), "pass": ok,
                             "source": "harness"},
            }
        )
    return nodes


def _md_section(text: str, name: str) -> str:
    """Cuerpo de una sección `## name` (hasta el próximo `## `)."""
    pattern = re.compile(
        rf"^##\s+{re.escape(name)}\s*$([\s\S]*?)(?=^##\s|\Z)", re.MULTILINE | re.IGNORECASE
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _numbered_items(block: str, limit: int = 12) -> list[str]:
    """Items de una lista numerada, con sus líneas de continuación."""
    items: list[str] = []
    current = ""
    for line in block.splitlines():
        stripped = line.strip()
        if re.match(r"^\d+\.\s+", stripped):
            if current:
                items.append(current)
            current = re.sub(r"^\d+\.\s+", "", stripped)
        elif current and stripped and not stripped.startswith(("#", "|", "-", "*")):
            current += " " + stripped
        elif current and not stripped:
            items.append(current)
            current = ""
        if len(items) >= limit:
            break
    if current and len(items) < limit:
        items.append(current)
    return [re.sub(r"\s+", " ", i).strip() for i in items if i.strip()][:limit]


def summary_knowledge_nodes(path: Path) -> list[dict]:
    """Convierte un summary de sesión (markdown) en nodos de conocimiento.

    El runner solo ve task/pass/tokens/duración: las DECISIONES de una sesión
    viven en la conversación, no en sus datos. Sin este paso el CKG nunca se
    entera de por qué se tomó un camino (medido: 42 ADRs ingeridos, 0 nodos de
    sesión).
    """
    text = path.read_text(encoding="utf-8")
    slug = _slug(re.sub(r"^summary-", "", path.stem))
    goal = re.sub(r"\s+", " ", _md_section(text, "Goal")).strip()
    constraints = _md_section(text, "Constraints & Preferences")
    next_steps = _numbered_items(_md_section(text, "Next Steps"))
    decisions = _numbered_items(_md_section(text, "Key Decisions"))
    blocked = _md_section(text, "Blocked")

    nodes: list[dict] = []
    if goal:
        nodes.append(
            {
                "id": f"session-{slug}",
                "label": f"Sesión {slug}: {goal[:200]}",
                "description": (
                    f"{goal[:900]} "
                    f"[resumen: {path.name}; "
                    f"{len(decisions)} decisión(es), {len(next_steps)} próximo(s) paso(s)]"
                ),
                "type": "implementation_state",
                "tags": ["session", "summary"],
                "metadata": {"source": str(path), "date": path.stem[-8:],
                             "decisions": len(decisions),
                             "blocked": bool(blocked)},
            }
        )
    for idx, decision in enumerate(decisions, 1):
        nodes.append(
            {
                "id": f"session-{slug}-decision-{idx:02d}",
                "label": decision[:240],
                "description": (
                    f"Decisión {idx} registrada en el resumen de sesión {path.name}."
                ),
                "type": "design-decision",
                "tags": ["decision", "session"],
                "metadata": {"source": str(path), "session": slug, "order": idx},
            }
        )
    if next_steps:
        nodes.append(
            {
                "id": f"session-{slug}-next",
                "label": f"Próximos pasos de la sesión {slug}",
                "description": " · ".join(f"{i}) {s}" for i, s in enumerate(next_steps, 1))[:900],
                "type": "implementation_state",
                "tags": ["session", "next-steps"],
                "metadata": {"source": str(path), "count": len(next_steps),
                             "constraints": constraints[:300]},
            }
        )
    return nodes


def print_summary(summary: dict) -> None:
    """Imprime el agregado; distingue wall (paralelo) de la suma por tarea."""
    def fmt(value: float | None, unit: str = "") -> str:
        if value is None:
            return "?"
        return f"{value:,.0f}{unit}" if float(value).is_integer() or unit else f"{value:,.1f}"

    gate_txt = (
        f"{summary['gate']}/{summary['tasks']} gate"
        if summary.get("gate_requested", True)
        else "gate n/a"
    )
    print(
        f"\nresumen · {summary['tasks']} tarea(s) · "
        f"{summary['pass']}/{summary['tasks']} pass · "
        f"{gate_txt} · wall {summary['wall_s']:,.1f} s"
    )
    if summary["models"]:
        print(f"  modelo: {' · '.join(summary['models'])}")
    if not summary["model_comparable"]:
        print("  ! modelos MEZCLADOS en la corrida: tokens no comparables")
    for label, key in (("tokens_in ", "tokens_in"), ("tokens_out", "tokens_out")):
        s = summary[key]
        if s["total"] is None:
            continue
        print(f"  {label} total {fmt(s['total'])} · mediana {fmt(s['median'])} · "
              f"min {fmt(s['min'])} · max {fmt(s['max'])}")
    d = summary["duration_s"]
    if d["median"] is not None:
        print(f"  duración   mediana {d['median']:,.1f} s · min {d['min']:,.1f} s · "
              f"max {d['max']:,.1f} s (suma {d['total']:,.1f} s)")


def test_command(task: dict, repo: Path, venv_python: str,
                 keepdb: bool = True) -> list[str]:
    """Comando de grading: explícito (`test_cmd`) o Django por defecto.

    `test_cmd` permite proyectos no-Django (TS/JS, Go…). `{python}` se
    sustituye por el intérprete del repo. Para Django se agrega `--keepdb`.
    """
    if task.get("test_cmd"):
        return [
            str(part).replace("{python}", str(repo / venv_python))
            for part in task["test_cmd"]
        ]
    cmd = [str(repo / venv_python), "manage.py", "test", task.get("test", "tests")]
    if keepdb:
        cmd.append("--keepdb")
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=1, help="tareas en paralelo (cada una con su sandbox)")
    ap.add_argument("--agent", default="build")
    ap.add_argument("--model", default=os.environ.get("HARNESS_MODEL"),
                    help="modelo a pinear; si se omite se usa el default de la "
                         "config y el CSV registra el que reporte la sesión")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--filter", default=None, help="substring del id de tarea")
    ap.add_argument("--check-tasks", action="store_true",
                    help="no llama al agente: verifica que la suite FALLE en el commit base")
    ap.add_argument("--no-mcp", action="store_true",
                    help="desactiva los MCP por config: sus índices apuntan al repo real")
    ap.add_argument("--no-sanitize", action="store_true",
                    help="no quita docstrings/comentarios del test inyectado")
    ap.add_argument("--keep", action="store_true", help="no borra los directorios de trabajo")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--opencode", default=shutil.which("opencode") or str(Path.home() / ".opencode/bin/opencode"))
    ap.add_argument("--out", default=None,
                    help="dónde dejar resultados (default ~/.local/share/walker/harness-runs)")
    ap.add_argument("--walker-project", default=None,
                    help="si se pasa, publica cada resultado en el TraceGraph vía API MCP")
    ap.add_argument("--summary", default=None,
                    help="markdown de resumen de sesión: sus decisiones y próximos "
                         "pasos se publican como conocimiento en el CKG del proyecto")
    ap.add_argument("--walker-gate", action="store_true",
                    help="además de pytest, pide el veredicto a walker.verify_predicates (project_root=sandbox)")
    ap.add_argument("--walker-strict", action="store_true",
                    help="aborta si el selftest read-only de walker falla")
    ap.add_argument("--venv-python", default=".venv/bin/python")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    if not args.walker_project and not args.check_tasks and not args.dry_run:
        detected = detect_project(repo)
        if detected and detected.get("project_id"):
            args.walker_project = detected["project_id"]
            origen = (
                "manifest"
                if Path(str(detected.get("repo_path", ""))).resolve() == repo
                else "registro"
            )
            print(f"  walker: proyecto detectado = {args.walker_project} "
                  f"({origen}: {detected.get('repo_path')})")
        else:
            print(f"  walker: sin proyecto registrado para {repo} — la corrida no "
                  f"publicará nada (usá --walker-project para forzarlo)")
    tasks = json.loads(Path(args.tasks).read_text())
    if args.filter:
        tasks = [t for t in tasks if args.filter in t["id"]]
    if not tasks:
        print("sin tareas que correr", file=sys.stderr)
        return 2

    stamp = time.strftime("%Y%m%dT%H%M%S")
    out_root = Path(args.out).expanduser() if args.out else Path.home() / ".local/share/walker/harness-runs"
    outdir = out_root / stamp
    if not args.dry_run:
        outdir.mkdir(parents=True, exist_ok=True)
    workroot = Path(os.environ.get("TMPDIR", "/tmp")) / f"eval-{stamp}"
    workroot.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PREFLIGHT_OFF"] = "1"  # el gate propio no debe contaminar la medición
    # El plugin global de flight-control no debe correr dentro de un eval: cada
    # hook lanza un CLI y trazaría sesiones sintéticas como si fueran trabajo.
    env["WALKER_FLIGHT_CONTROL"] = "0"
    env.pop("OPENCODE_CLI_RUN_MODE", None)
    venv_bin = (repo / args.venv_python).parent
    env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"  # `python` = el del venv

    rows: list[dict] = []
    lock = threading.Lock()
    walker_ctx: dict = {}
    print(f"run {stamp} · {len(tasks)} tarea(s) × k={args.k} · jobs={args.jobs} · salida en {outdir}")
    effective_model = args.model or agent_default_model(args.agent)
    if args.model:
        print(f"  modelo: {args.model} (pinneado)")
    elif effective_model:
        print(f"  modelo: {effective_model} (del agente '{args.agent}', NO pinneado) "
              f"— pinear con --model o HARNESS_MODEL para comparar tokens")
    else:
        print("  modelo: NO pinneado — se usa el default de la config; el CSV "
              "registra el que reporte la sesión. Para comparar tokens entre "
              "corridas, pinear con --model o HARNESS_MODEL.")

    if args.walker_project and not args.check_tasks:
        selftest_path = Path(__file__).resolve().parent / "selftest.py"
        report: dict = {}
        try:
            proc = subprocess.run(
                [sys.executable, str(selftest_path), "--project", args.walker_project,
                 "--read-only", "--json"],
                capture_output=True, text=True, timeout=180,
            )
            report = json.loads(proc.stdout or "{}")
            failed = [c["check"] for c in report.get("checks", [])
                      if c.get("required") and not c.get("ok")]
        except Exception as exc:
            failed = [f"selftest no ejecutable: {exc}"]
        if failed:
            print(f"  ! walker selftest: {', '.join(failed)}", file=sys.stderr)
            if args.walker_strict:
                print("  ! abortando: --walker-strict y walker no está sano", file=sys.stderr)
                return 2
        else:
            print(f"  walker: selftest read-only ok ({len(report.get('checks', []))} checks)")

    if args.walker_project and not args.check_tasks:
        try:
            from walker_client import WalkerClient

            with WalkerClient() as wc:
                projects = wc.call("list_projects", {}).get("projects") or []
                if args.walker_project not in projects:
                    print(f"  ! walker: '{args.walker_project}' no está registrado", file=sys.stderr)
                health = wc.call("status", {"project_id": args.walker_project})
                stamp_watch = (health.get("watcher") or {}).get("timestamp")
                if stamp_watch:
                    try:
                        from datetime import datetime, timezone
                        age = (datetime.now(timezone.utc) - datetime.fromisoformat(stamp_watch)).days
                        if age > 7:
                            print(f"  ! walker: el watcher de {args.walker_project} tiene {age} días", file=sys.stderr)
                    except ValueError:
                        pass
                sprint = wc.call("resolve_project_sprint", {"project_id": args.walker_project})
                walker_ctx["sprint_id"] = (sprint.get("spring_id") or sprint.get("sprint_id")
                                           or (sprint.get("data") or {}).get("sprint_id"))
                print(f"  walker: preflight ok · sprint={walker_ctx.get('sprint_id')} · "
                      f"proyecto={'registrado' if args.walker_project in projects else 'NO registrado'}")
        except Exception as exc:
            print(f"  ! walker: preflight falló ({exc}); la corrida sigue sin ingesta", file=sys.stderr)

    if args.walker_project and not args.check_tasks and not args.dry_run:
        try:
            from walker_client import WalkerClient

            with WalkerClient() as wc:
                for task in tasks:
                    if task.get("disabled"):
                        continue
                    wc.call("implement_task", {
                        "project_id": args.walker_project,
                        "task_id": f"eval:{task['id']}",
                        "objective": task["prompt"][:400],
                    })
                print(f"  walker: {sum(1 for t in tasks if not t.get('disabled'))} tarea(s) registradas en el ciclo")
        except Exception as exc:
            print(f"  ! walker: no se pudieron registrar las tareas: {exc}", file=sys.stderr)

    def process(task: dict, k: int, base: str) -> None:
        name = f"{task['id']}-k{k}"
        wt = workroot / name
        ok, why = materialize(repo, base, wt, env)
        if not ok:
            print(f"  ! {name}: no se pudo materializar el base: {why[:200]}", file=sys.stderr)
            return

        envfile = repo / ".env"
        if envfile.exists():
            shutil.copy2(envfile, wt / ".env")

        err = inject_tests(repo, task, wt, env, not args.no_sanitize)
        if err:
            print(f"  ! {name}: {err}", file=sys.stderr)

        # ── Modo workflow (opt-in): el agente camina el workflow de walker ──
        # El eval directo usa --no-mcp (los índices apuntan al repo real), así
        # que no puede medir plantillas. El modo workflow habilita MCP y
        # registra el sandbox como proyecto, para que el agente pueda llamar a
        # `walker <id>/status` y ejecutar el sub-estado actual.
        is_workflow = task.get("mode") == "workflow"
        walker_project_id: str | None = None
        if is_workflow:
            if args.no_mcp:
                print(f"  ! {name}: modo workflow requiere MCP; --no-mcp se ignora", file=sys.stderr)
            try:
                from walker_client import WalkerClient
                with WalkerClient() as wc:
                    prep = wc.call("prepare", {"path": str(wt)})
                    walker_project_id = prep.get("project_id") or wt.name
                    env["EVAL_WALKER_PROJECT"] = walker_project_id
                    print(f"  walker: sandbox registrado como '{walker_project_id}'")
            except Exception as exc:
                print(f"  ! {name}: prepare del sandbox falló: {exc}", file=sys.stderr)
                walker_project_id = wt.name
                env["EVAL_WALKER_PROJECT"] = walker_project_id
            # Sembrar el conocimiento del proyecto real para que la sección
            # CONOCIMIENTO DEL PROYECTO no llegue vacía.
            seeded = _seed_knowledge(repo, wt, env)
            print(f"  walker: conocimiento sembrado ({seeded} docs)")

        if args.no_mcp and not is_workflow:
            (wt / "opencode.json").write_text(json.dumps(
                {"mcp": {srv: {"enabled": False} for srv in MCP_SERVERS}}, indent=2))

        run(["git", "init", "-q", "-b", "main"], wt, env, 30)
        run(["git", "add", "-A"], wt, env, 120)
        run(["git", "-c", "user.email=eval@local", "-c", "user.name=eval",
             "commit", "-qm", "base"], wt, env, 120)

        test_cmd = test_command(task, repo, args.venv_python,
                                keepdb=not args.check_tasks)

        if args.check_tasks:
            test_rc, test_out, test_took = run(test_cmd, wt, env, args.timeout)
            (outdir / f"{name}.base-test.log").write_text("\n".join(test_out.splitlines()[-SUMMARY_KEEP_LINES:]))
            broken = "No module named" in test_out or "Failed to import test module" in test_out
            valid = test_rc != 0 and not broken
            motivo = ("test no importa en base (falta el archivo o un fixture)"
                      if broken else ("bug presente" if valid else "PASA en base: tarea inválida"))
            print(f"  {'OK ' if valid else 'OJO'} {name:<28} base rc={test_rc} "
                  f"({round(test_took, 1)}s) → {motivo}")
            if not args.keep:
                drop_worktree(repo, wt, env)
            return

        cmd = [args.opencode, "run", "--format", "json", "--archived", "--standalone",
               "--agent", args.agent]
        if args.model:
            cmd += ["--model", args.model]
        if is_workflow:
            prompt = task.get("workflow_prompt") or task.get("prompt", "")
            prompt = prompt.replace("{project_id}", walker_project_id or wt.name)
            if "{project_id}" not in (task.get("workflow_prompt") or task.get("prompt", "")):
                prompt = f"Proyecto walker: {walker_project_id}\n\n" + prompt
        else:
            prompt = task["prompt"]
        cmd += [prompt]

        code, output, took = run(cmd, wt, env, args.timeout)
        (outdir / f"{name}.jsonl").write_text(output)

        seen = SESSION_RE.findall(output)
        session_id = seen[-1] if seen else None

        test_rc, test_out, test_took = run(test_cmd, wt, env, args.timeout)
        (outdir / f"{name}.test.log").write_text("\n".join(test_out.splitlines()[-SUMMARY_KEEP_LINES:]))

        gate = None
        if args.walker_project and args.walker_gate:
            try:
                import shlex

                from walker_client import WalkerClient

                with WalkerClient() as wc:
                    verdict = wc.call("verify_predicates", {
                        "project_id": args.walker_project,
                        "task_id": f"eval:{task['id']}",
                        "project_root": str(wt),
                        "predicates": [{"type": "test_pass", "command": shlex.join(test_cmd)}],
                    })
                gate = int(bool((verdict.get("data") or {}).get("all_satisfied")))
            except Exception as exc:
                print(f"  ! walker gate {name}: {exc}", file=sys.stderr)

        _, diff_out, _ = run(["git", "diff", "HEAD"], wt, env, 60)
        _, status_out, _ = run(["git", "status", "--porcelain"], wt, env, 60)
        (outdir / f"{name}.diff").write_text(diff_out)
        (outdir / f"{name}.status").write_text(status_out)

        usage = session_usage(session_id)
        passed = code == 0 and test_rc == 0 and gate in (None, 1)
        row = {
            "run": stamp, "task": task["id"], "k": k, "base": base[:8],
            "agent": args.agent, "model": usage["model"] or effective_model or "(config)",
            "pass": int(passed), "agent_rc": code, "test_rc": test_rc,
            "walker_gate": gate,
            "duration_s": round(took + test_took, 1),
            "session_id": session_id or "",
            **usage,
        }
        with lock:
            rows.append(row)
        print(f"  {'PASS' if passed else 'FAIL'}  {name:<28} {row['duration_s']:>7}s  "
              f"cost={usage['cost_usd'] if usage['cost_usd'] is not None else '?'}")

        if not args.keep:
            drop_worktree(repo, wt, env)

    pairs: list[tuple[dict, int, str]] = []
    for task in tasks:
        if task.get("disabled"):
            print(f"  - {task['id']}: deshabilitada", file=sys.stderr)
            continue
        base = resolve_base(repo, task)
        if not base:
            print(f"  ! {task['id']}: sin base_commit ni fix_commit resoluble, se omite", file=sys.stderr)
            continue
        for k in range(1, args.k + 1):
            pairs.append((task, k, base))

    if args.dry_run:
        for task, k, base in pairs:
            print(f"  [dry] {task['id']}-k{k}: {task.get('test', 'tests')} @ {base[:8]}")
        return 0

    if args.jobs > 1 and not args.check_tasks:
        started = time.time()
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            list(pool.map(lambda pair: process(*pair), pairs))
    else:
        started = time.time()
        for pair in pairs:
            process(*pair)

    rows.sort(key=lambda r: (r["task"], r["k"]))
    if not rows:
        # Sin corridas, pero con --summary: el resumen igual es conocimiento.
        if args.walker_project and args.summary:
            try:
                from walker_client import WalkerClient

                nodes = summary_knowledge_nodes(Path(args.summary).expanduser())
                if nodes:
                    with WalkerClient() as walker:
                        walker.call("documenting_knowledge",
                                    {"project_id": args.walker_project, "nodes": nodes})
                    print(f"  walker: {len(nodes)} nodo(s) de sesión publicados "
                          f"(sin corridas)")
            except Exception as exc:
                print(f"  ! walker: no se pudo publicar el summary: {exc}",
                      file=sys.stderr)
        return 0

    csv_path = outdir / "results.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    if args.walker_project:
        try:
            from walker_client import WalkerClient

            with WalkerClient() as walker:
                publicado = 0
                for r in rows:
                    try:
                        walker.call("trace_push", {
                            "event_type": "eval.result",
                            "source": "harness",
                            "task_id": r["task"],
                            "sprint_id": walker_ctx.get("sprint_id"),
                            "node": "eval",
                            "project_id": args.walker_project,
                            "data": {key: r[key] for key in (
                                "run", "k", "pass", "agent_rc", "test_rc", "walker_gate", "duration_s",
                                "tokens_in", "tokens_out", "model", "base", "session_id",
                            )},
                        })
                        publicado += 1
                    except Exception as exc:
                        print(f"  ! walker: trace_push {r['task']} falló: {exc}", file=sys.stderr)
                resumen = f"{sum(r['pass'] for r in rows)}/{len(rows)} pass · run {stamp}"
                for tool, payload in (
                    ("capture", {"project_id": args.walker_project, "evidence": resumen,
                                 "artifacts": [str(csv_path)]}),
                    ("submit", {"project_id": args.walker_project,
                                "decision": resumen, "evidence": str(csv_path)}),
                ):
                    try:
                        walker.call(tool, payload)
                    except Exception as exc:
                        print(f"  ! walker: {tool} falló: {exc}", file=sys.stderr)

                # Conocimiento (CKG): antes el harness SOLO publicaba trace, así
                # que el CKG del proyecto no sabía nada de las corridas.
                knowledge = eval_knowledge_nodes(rows, stamp)
                sesion = 0
                if args.summary:
                    summary_path = Path(args.summary).expanduser().resolve()
                    if summary_path.exists():
                        extra = summary_knowledge_nodes(summary_path)
                        sesion = len(extra)
                        knowledge += extra
                    else:
                        print(f"  ! walker: summary no encontrado: {summary_path}",
                              file=sys.stderr)
                if knowledge:
                    try:
                        walker.call("documenting_knowledge",
                                    {"project_id": args.walker_project, "nodes": knowledge})
                        print(f"  walker: {len(knowledge)} nodo(s) de conocimiento "
                              f"publicados ({len(knowledge) - sesion} de eval"
                              f"{f', {sesion} de sesión' if sesion else ''})")
                    except Exception as exc:
                        print(f"  ! walker: documenting_knowledge falló: {exc}",
                              file=sys.stderr)
            print(f"  walker: {publicado}/{len(rows)} resultado(s) publicados · {resumen}")
        except Exception as exc:  # nunca romper la medición por la ingesta
            print(f"  ! walker: no se pudieron publicar los resultados: {exc}", file=sys.stderr)

    total = len(rows)
    ok = sum(r["pass"] for r in rows)
    cost = sum(r["cost_usd"] or 0 for r in rows)
    summary = summarize(rows, stamp, time.time() - started)
    (outdir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    )
    print_summary(summary)
    print(f"\n{ok}/{total} PASS ({100 * ok / total:.0f}%) · costo registrado ${cost:.2f} · {csv_path}")
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
