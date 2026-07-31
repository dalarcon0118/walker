"""task/review.py — Language-agnostic code review via analyze_refactored.py."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .common import eprint


def _run(cmd: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _find_analyze_script() -> Path | None:
    candidates = [
        Path(__file__).parent.parent.parent / "tools" / "analyze_refactored.py",
        Path(__file__).parent.parent / "tools" / "analyze_refactored.py",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def cmd_review(args: list[str]) -> int:
    """Language-agnostic code review via analyze_refactored.py.

    Uso: task.py review --fast|--deep <chain_dir> <file1> [file2 ...]

    Modos:
      --fast → qlty metrics only (rápido, multilenguaje)
      --deep → qlty metrics + smells + check + ast-grep (completo)

    Exit codes:
      0 → Code review limpio
      1 → Code review con hallazgos
      2 → Error
    """
    if not args or not any(a.startswith("--") for a in args[:2]):
        eprint("Uso: task.py review --fast|--deep <chain_dir> <file1> [file2 ...]")
        return 2

    mode = "deep"
    rest = []
    for a in args:
        if a == "--fast":
            mode = "fast"
        elif a == "--deep":
            mode = "deep"
        else:
            rest.append(a)

    if len(rest) < 2:
        eprint("Uso: task.py review --fast|--deep <chain_dir> <file1> [file2 ...]")
        return 2

    rest[0]
    paths = rest[1:]

    analyze_script = _find_analyze_script()
    if not analyze_script:
        eprint("❌ analyze_refactored.py no encontrado")
        return 2

    print(f"🔍 Code review ({mode}) para {len(paths)} archivos/dirs")
    for p in paths:
        print(f"   - {p}")

    cmd = [sys.executable, str(analyze_script), "--json", f"--{mode}"] + paths

    try:
        r = _run(cmd, timeout=180)
    except subprocess.TimeoutExpired:
        eprint("❌ Timeout en code review (>180s)")
        return 1
    except Exception as e:
        eprint(f"❌ Error ejecutando code review: {e}")
        return 1

    if r.returncode != 0:
        eprint(f"❌ analyze_refactored.py falló (exit {r.returncode})")
        if r.stderr:
            print(r.stderr[:500])
        return 1

    stdout = r.stdout.strip()
    if not stdout:
        print("✅ Code review OK (sin findings)")
        return 0

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        print(r.stdout[:1000])
        return 0

    findings: list[str] = []
    for f in data.get("files", []):
        for c in f.get("checks", []):
            msg = c.get("rule_id") or c.get("message", "?")
            findings.append(f"  {f.get('file')}: {msg}")
        for s in f.get("smells", []):
            msg = s.get("rule_id") or s.get("message", "?")
            findings.append(f"  {f.get('file')}: smell:{msg}")

    total_checks = data.get("total_checks", 0)
    total_smells = data.get("total_smells", 0)

    if findings:
        print(f"❌ Code review encontró {total_checks} checks / {total_smells} smells:")
        for line in findings[:20]:
            print(line)
        return 1

    if mode == "fast":
        metrics_summary = _format_fast_metrics(data)
        if metrics_summary:
            print(metrics_summary)
        print(f"✅ Code review OK: {total_checks} checks, {total_smells} smells")
    else:
        print(f"✅ Code review OK: {total_checks} checks, {total_smells} smells")
    return 0


def _format_fast_metrics(data: dict) -> str:
    lines = ["📊 Métricas (fast mode):"]
    for f in data.get("files", []):
        name = f.get("file", "?")
        loc = f.get("loc", 0)
        cyclo = f.get("cyclo", 0)
        complexity = f.get("complexity", 0)
        lcom = f.get("lcom", 0)
        funcs = f.get("funcs", 0)
        if loc or cyclo or funcs:
            lines.append(
                f"   {name}: LOC={loc}, cyclo={cyclo}, complexity={complexity}, LCOM={lcom}, funcs={funcs}"
            )
    total_loc = sum(f.get("loc", 0) for f in data.get("files", []))
    total_cyclo = sum(f.get("cyclo", 0) for f in data.get("files", []))
    lines.append(
        f"   TOTAL: LOC={total_loc}, cyclo={total_cyclo}, files={data.get('files_analyzed', 0)}"
    )
    return "\n".join(lines)
