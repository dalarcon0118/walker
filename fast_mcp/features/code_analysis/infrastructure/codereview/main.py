"""CLI entry point for code review (sin qlty, ADR-WALKER-054)."""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

from .config import AST_GREP_AVAILABLE
from .analysis import run_analysis
from .quality import c_lower_better, format_rating


def _update_progress(msg: str) -> None:
    """Write progress to stderr for JSON mode, stdout otherwise."""
    if "--json" in sys.argv:
        print(msg, file=sys.stderr)
    else:
        print(msg)


def main() -> int:
    """CLI entry point."""
    json_mode, fix_mode, deep_mode, fast_mode, args = _parse_cli_args()
    if deep_mode and fast_mode:
        print("❌ --fast y --deep son mutuamente excluyentes", file=sys.stderr)
        return 2

    files_to_analyze = [Path(a) for a in args if not a.startswith("-")]
    mode = "deep" if deep_mode or not fast_mode else "fast"

    if not files_to_analyze:
        print(
            "Uso: python3 -m codereview [--json] [--fast|--deep] <file1> [file2 ...]",
            file=sys.stderr,
        )
        return 1

    files_to_analyze = [f for f in files_to_analyze if f.exists()]
    if not files_to_analyze:
        print("❌ Ningún archivo válido encontrado.", file=sys.stderr)
        return 1

    # ── Auto-fix mode ──
    if fix_mode:
        _update_progress(
            "  ℹ️  Auto-fix ya no está disponible (qlty eliminado). "
            "Correr 'ast-grep' o un formatter propio del lenguaje."
        )

    # ── JSON mode ──
    if json_mode:
        output = run_analysis(files_to_analyze, mode=mode)
        print(_json.dumps(output, indent=2, ensure_ascii=False))
        return 0

    # ── Terminal display mode ──
    print("=" * 88)
    print("  \033[1m📊 Code Review — ANÁLISIS MULTILENGUAJE\033[0m")
    print(
        f"  Modo: {mode}  |  "
        f"ast-grep: {'\033[32m✓\033[0m' if AST_GREP_AVAILABLE else '\033[31m✗\033[0m'}  |  "
        f"Archivos: {len(files_to_analyze)}  |  "
        f"Lenguajes: {len({f.suffix for f in files_to_analyze})} detectados"
    )
    print("=" * 88)

    # Run analysis
    output = run_analysis(files_to_analyze, mode=mode)
    results = output.get("files", [])

    total_loc = 0
    for fp in files_to_analyze:
        if not fp.exists():
            continue
        entry = next((r for r in results if fp.name in r.get("file", "")), None)
        if not entry:
            continue
        total_loc += _display_file(fp, entry)

    # ── Summary ──
    _display_summary(output, results, total_loc)
    return 0


def _parse_cli_args() -> tuple[bool, bool, bool, bool, list[str]]:
    """Parsea flags de CLI: --json, --fix, --deep, --fast."""
    json_mode = False
    fix_mode = False
    deep_mode = False
    fast_mode = False

    args = sys.argv[1:]
    if "--json" in args:
        json_mode = True
        args.remove("--json")
    if "--fix" in args:
        fix_mode = True
        args.remove("--fix")
    if "--deep" in args:
        deep_mode = True
        args.remove("--deep")
    if "--fast" in args:
        fast_mode = True
        args.remove("--fast")
    return json_mode, fix_mode, deep_mode, fast_mode, args


def _display_file(fp: Path, entry: dict) -> int:
    """Imprime el reporte de un archivo. Devuelve su LOC contribuido."""
    cyclo = entry.get("cyclo", 0)
    complexity = entry.get("complexity", 0)
    lcom = entry.get("lcom", 0)
    loc = entry.get("loc", 0)
    func_count = entry.get("funcs", 0)
    raw_loc = loc or len([l for l in fp.read_text().splitlines() if l.strip()])

    lang_tag = f"\033[90m{fp.suffix or '?'}\033[0m"
    print(f"\n{'─' * 88}")
    print(f"  \033[1m📄 {fp.name}\033[0m  {lang_tag}")
    print(f"{'─' * 88}")
    print(
        f"  NLOC: {loc}  |  {c_lower_better('Cyclo', cyclo, 30, 60)}  |  "
        f"{c_lower_better('Cog Cpx', complexity, 20, 50)}  |  "
        f"Funcs: {func_count}  |  {c_lower_better('LCOM', lcom, 5, 20)}"
    )

    # Coupling
    print(f"  Coupling: Ce={entry.get('ce', 0)}, Ca={entry.get('ca', 0)}")

    # Deep findings (ast-grep)
    deep = entry.get("deep_analysis", {})
    if deep.get("deep_findings"):
        print(
            f"  \033[1m🔬 Análisis profundo ({deep['total_deep']} hallazgos):\033[0m"
        )
        for df in deep["deep_findings"][:5]:
            severity_color = {
                "critical": "\033[31m",
                "high": "\033[33m",
                "medium": "\033[33m",
                "low": "\033[90m",
            }.get(df.get("severity", ""), "\033[90m")
            print(
                f"    {severity_color}[{df.get('severity', '?')}]\033[0m {df.get('label', df.get('pattern_id', '?'))}"
            )
            for m in df.get("matches", [])[:2]:
                print(f"      L{m['line']}: {m['text'][:80]}")
            if df.get("count", 0) > 2:
                print(f"      ... +{df['count'] - 2} more")

    # Quality metrics
    qm = entry.get("quality", {})
    if qm:
        qm_lines = qm.get("lines", 0)
        qm_nloc = qm.get("nloc", 0)
        qm_cyclo = qm.get("cyclomatic_complexity", 0)
        qm_debt = qm.get("tech_debt_hours", 0)
        qm_rating = qm.get("maintainability_rating", "?")
        qm_sec = qm.get("security_rating", "?")
        print(
            f"  \033[1m📊 Quality:\033[0m Lines: {qm_lines}  |  NLOC: {qm_nloc}  |  "
            f"Cyclo: {qm_cyclo}  |  Debt: {qm_debt}h  |  "
            f"Maint: {format_rating(qm_rating)}  |  Security: {format_rating(qm_sec)}"
        )
    return raw_loc


def _display_summary(output: dict, results: list, total_loc: int) -> None:
    """Imprime el resumen agregado al final del reporte."""
    print()
    print("=" * 88)
    print("  \033[1m📊 RESUMEN\033[0m")
    print(
        f"  Archivos: {output.get('files_analyzed', 0)}  |  "
        f"LOC total: {total_loc}  |  "
        f"Smells: {output.get('total_smells', 0)}  |  "
        f"Checks: {output.get('total_checks', 0)}"
    )
    print(
        f"  ast-grep: {'✅ disponible' if AST_GREP_AVAILABLE else '❌ no disponible'}"
    )

    # Summary rows
    if results:
        agg_loc = sum(r.get("loc", 0) for r in results)
        agg_cyclo = sum(r.get("cyclo", 0) for r in results)
        agg_complexity = sum(r.get("complexity", 0) for r in results)
        agg_smells = sum(r.get("smells_count", 0) for r in results)
        print(
            f"  Aggregated: LOC={agg_loc}, Cyclo={agg_cyclo}, "
            f"Complexity={agg_complexity}, Smells={agg_smells}"
        )
        if agg_cyclo > 0:
            print(f"  Average CC per file: {agg_cyclo / max(len(results), 1):.1f}")
        if agg_smells > 0:
            all_checks = [c for r in results for c in r.get("checks", [])]
            warnings = sum(1 for c in all_checks if c.get("level") == "warning")
            errors = sum(1 for c in all_checks if c.get("level") == "error")
            print(
                f"  Issues: {errors} errors, {warnings} warnings, "
                f"{sum(1 for c in all_checks if c.get('level') not in ('warning', 'error'))} notes"
            )

    print("=" * 88)


if __name__ == "__main__":
    sys.exit(main())
