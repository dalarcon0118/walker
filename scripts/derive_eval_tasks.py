"""scripts/derive_eval_tasks.py — propone tareas de eval desde el historial git.

Problema: la suite actual está saturada (8/8 pass). Para medir mejoras hace
falta un benchmark donde el agente FALLE en el baseline; escribir 20-40 tareas a
mano es caro. El historial ya tiene la materia prima: commits que arreglaron un
bug y agregaron/cambiaron tests. Cada uno es un candidato con baseline = su
padre.

El script NO garantiza que la tarea sea válida: genera el JSON y deja que
`harness/runner.py --check-tasks` verifique que el test FALLA en el base (si
pasa, la tarea no sirve y se descarta).

Uso:
    python3 scripts/derive_eval_tasks.py --repo <repo> --since 2026-08-01 \
        --limit 40 --out /tmp/tasks-candidates.json
    python3 harness/runner.py --repo <repo> --tasks /tmp/tasks-candidates.json --check-tasks
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java"}
SKIP_SUBJECTS = re.compile(r"\b(revert|merge|wip|chore|docs?|release|bump)\b", re.I)
CONVENTIONAL = re.compile(r"^[a-z]+(\([^)]*\))?!?:\s*", re.I)


def parse_git_log(text: str) -> list[dict]:
    """`git log --name-only --pretty=format:__C__%h|%s` → commits con archivos."""
    commits: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        if line.startswith("__C__"):
            if current:
                commits.append(current)
            sha, _, subject = line[5:].partition("|")
            current = {"sha": sha.strip(), "subject": subject.strip(), "files": []}
        elif line.strip() and current is not None:
            current["files"].append(line.strip())
    if current:
        commits.append(current)
    return commits


def is_test_file(path: str) -> bool:
    name = Path(path).name.lower()
    return (
        "test" in name
        or "/tests/" in f"/{path.lower()}"
        or name.startswith("test_")
        or name.endswith(("_test.py", ".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"))
    )


def is_source_file(path: str) -> bool:
    return Path(path).suffix.lower() in CODE_EXT and not is_test_file(path)


def django_test_label(path: str) -> str:
    """`tests/incident/test_writes.py` → `tests.incident.test_writes`."""
    p = Path(path)
    parts = list(p.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def clean_subject(subject: str) -> str:
    text = CONVENTIONAL.sub("", subject).strip()
    return text[:300]


def build_task(commit: dict, kind: str = "django") -> dict | None:
    files = commit.get("files") or []
    tests = [f for f in files if is_test_file(f) and Path(f).suffix.lower() in CODE_EXT]
    sources = [f for f in files if is_source_file(f)]
    if not tests or not sources:
        return None
    if len(files) > 25:
        return None  # demasiado amplio: el baseline no aísla el bug
    subject = commit.get("subject", "")
    if SKIP_SUBJECTS.search(subject) or not subject:
        return None

    label = django_test_label(tests[0]) if kind == "django" else tests[0]
    # El prompt NO puede salir del subject del commit: "expone bono_kind y
    # count_scope en reward_rule" ES la solución (medido en 29/29 derivadas de
    # backend). El síntoma es el test que falla; el agente lo corre y lo lee.
    task = {
        "id": f"derived-{commit['sha'][:8]}",
        "fix_commit": commit["sha"],
        "test_from": commit["sha"],
        "test_files": tests,
        "prompt": (
            f"El test `{label}` falla en este checkout. Encontrá la causa y "
            "hacelo pasar sin modificar los tests."
        ),
        "derived_from": subject,
    }
    if kind == "django":
        task["test"] = label
    else:
        task["test_cmd"] = ["node", "--test"]
    return task


def git_log(repo: Path, since: str | None) -> str:
    cmd = ["git", "log", "--no-merges", "--name-only", "--pretty=format:__C__%h|%s"]
    if since:
        cmd.insert(2, f"--since={since}")
    proc = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"git log falló: {proc.stderr.strip()[:200]}")
    return proc.stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--since", default=None)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--kind", choices=["django", "node"], default="django")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    commits = parse_git_log(git_log(repo, args.since))
    tasks = []
    seen_labels = set()
    for commit in commits:
        task = build_task(commit, kind=args.kind)
        if not task:
            continue
        key = task.get("test") or ",".join(task.get("test_files", []))
        if key in seen_labels:
            continue  # mismo módulo de test: no duplicar
        seen_labels.add(key)
        tasks.append(task)
        if len(tasks) >= args.limit:
            break

    Path(args.out).write_text(json.dumps(tasks, indent=2, ensure_ascii=False) + "\n")
    print(f"commits: {len(commits)} · tareas propuestas: {len(tasks)} → {args.out}")
    for task in tasks[:10]:
        print(f"  {task['id']:<24} {task['test'] if 'test' in task else task['test_files'][0]}")
    if len(tasks) < 20:
        print(f"\n! solo {len(tasks)} candidatas: revisá --since o el repo (se buscan "
              "commits que toquen tests y fuente a la vez)")
    print("\nvalidá con: harness/runner.py --repo <repo> --tasks <out> --check-tasks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
