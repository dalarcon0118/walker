"""prompts/evidence.py — Motor de evidencia + Meta-Instruction.

Migrado de to_clean_next/scripts/prompt_engine.py (legacy).

Patrón "Evidence + Meta-Instruction": genera bloques de prompt
auto-contextualizados donde cada predicate/check incluye EVIDENCIA
CRUDA (stdout, exit codes, líneas grep, símbolos) + assessment
+ meta-instruction para que el LLM presente los datos al usuario.

Es el corazón de los USER GATES: el agente necesita evidencia real
para responder "QUÉ SUCEDIÓ" y "POR QUÉ" antes de pedir la decisión.
"""

from __future__ import annotations


# ── Formato de bloque de evidencia cruda ─────────────────────────


def format_detail_block(detail: dict) -> str:
    """Formatea un bloque de evidencia cruda desde el info dict de verify.

    El detail dict contiene lo que la verificación devuelve en su info:
    method, command, exit_code, stdout, stderr, symbol, file, pattern, etc.
    """
    method = detail.get("method", "?")
    lines: list[str] = []

    if method == "shell":
        lines.append("       ── Shell ──")
        lines.append(f"       Comando: {detail.get('command', '?')}")
        lines.append(f"       Exit code: {detail.get('exit_code', '?')}")
        stdout = detail.get("stdout", "")
        if stdout:
            lines.append("       stdout:")
            for line in stdout.splitlines()[:5]:
                lines.append(f"         {line}")
        stderr = detail.get("stderr", "")
        if stderr:
            lines.append(f"       stderr ({len(stderr.splitlines())} lines):")
            for line in stderr.splitlines()[:3]:
                lines.append(f"         {line}")

    elif method in ("symbol_exists", "symbol_not_exists"):
        lines.append("       ── Symbol ──")
        lines.append(f"       Símbolo: {detail.get('symbol', '?')}")
        if detail.get("file"):
            lines.append(f"       Archivo: {detail['file']}")

    elif method == "command_output":
        lines.append("       ── Command Output ──")
        lines.append(f"       Comando: {detail.get('command', '?')}")
        lines.append(f'       Buscaba: "{detail.get("contains", "?")}"')
        found = detail.get("found", False)
        lines.append(f"       Encontrado: {'✅ sí' if found else '❌ no'}")
        stdout = detail.get("stdout", "")
        if stdout:
            lines.append("       stdout:")
            for line in stdout.splitlines()[:5]:
                lines.append(f"         {line}")
        stderr = detail.get("stderr", "")
        if stderr:
            lines.append("       stderr:")
            for line in stderr.splitlines()[:3]:
                lines.append(f"         {line}")

    elif method == "test_pass":
        lines.append("       ── Test ──")
        lines.append(f"       Comando: {detail.get('command', '?')}")
        lines.append(f"       Exit code: {detail.get('exit_code', '?')}")

    elif method == "ast-grep":
        lines.append("       ── Ast-grep ──")
        lines.append(f"       Pattern: {detail.get('pattern', '?')}")
        lines.append(f"       Tipo: {detail.get('type', '?')}")
        if detail.get("file_hint"):
            lines.append(f"       Archivo: {detail['file_hint']}")

    elif method == "error":
        lines.append(f"       ⚠️  Error: {detail.get('error', '?')[:120]}")

    if detail.get("error") and method not in ("error",):
        lines.append(f"       ⚠️  Error: {detail['error'][:100]}")

    return "\n".join(lines)


def format_predicate_block(
    predicate_text: str,
    passed: bool,
    detail: dict,
    index: int,
    total: int,
) -> str:
    """Formatea un bloque completo de predicate: [evidencia] → [assessment]."""
    status_icon = "✅" if passed else "❌"
    status_text = "CUMPLE" if passed else "NO CUMPLE"
    label = detail.get("label", str(predicate_text)[:70])
    method = detail.get("method", "?")

    lines = [
        f"  [{index}/{total}] {status_icon} {label}",
        f"       Método: {method}",
    ]

    evidence = format_detail_block(detail)
    if evidence:
        lines.append(evidence)

    lines.append(f"       → {status_icon} {status_text}")
    lines.append("")
    return "\n".join(lines)


def build_evidence_blocks(checks: dict) -> str:
    """Construye secciones de evidencia: passed y failed predicates."""
    parts = []
    passed = checks.get("passed_items", [])
    if passed:
        blocks = ["", "─" * 70, "✅ EVIDENCIA — Predicates que CUMPLEN", "─" * 70, ""]
        for i, p in enumerate(passed, 1):
            blocks.append(
                format_predicate_block(
                    p.get("predicate", ""), True, p.get("detail", {}), i, len(passed)
                )
            )
        parts.append("\n".join(blocks))
    failed = checks.get("failed", [])
    if failed:
        blocks = [
            "",
            "─" * 70,
            "❌ EVIDENCIA — Predicates que NO CUMPLEN",
            "─" * 70,
            "",
        ]
        for i, f in enumerate(failed, 1):
            blocks.append(
                format_predicate_block(
                    f.get("predicate", ""), False, f.get("detail", {}), i, len(failed)
                )
            )
        parts.append("\n".join(blocks))
    return "\n".join(parts)


def build_ac_section(ac_results: list) -> str:
    """Construye la sección de acceptance criteria."""
    if not ac_results:
        return ""
    passed = sum(1 for ac in ac_results if ac["passed"])
    lines = [
        "",
        "─" * 70,
        f"🎯 ACCEPTANCE CRITERIA: {passed}/{len(ac_results)}",
        "─" * 70,
    ]
    for ac in ac_results:
        icon = "✅" if ac["passed"] else "❌"
        lines.append(f"  {icon} {ac['criterion'][:80]}")
    return "\n".join(lines)


def build_qg_section(qg_results: list) -> str:
    """Construye la sección de quality gates."""
    if not qg_results:
        return ""
    passed = sum(1 for qg in qg_results if qg["passed"])
    lines = ["", "─" * 70, f"🔒 QUALITY GATES: {passed}/{len(qg_results)}", "─" * 70]
    for qg in qg_results:
        icon = "✅" if qg["passed"] else "❌"
        lines.append(f"  {icon} {qg['gate'][:80]}")
    return "\n".join(lines)


def build_verdict(
    passed: int,
    total: int,
    failed_items: list,
    can_confirm: bool,
) -> str:
    """Veredicto final: decisión + siguiente paso."""
    if can_confirm:
        return (
            f"✅ Decisión: TASK COMPLETA. {passed}/{total} verificaciones superan.\n"
            f"   → Puedes aprobar y continuar al siguiente paso."
        )
    elif passed > 0:
        return (
            f"⚠️  Decisión: AVANCE PARCIAL. {passed}/{total} pasan.\n"
            f"   Revisa los fallos y refina antes de continuar."
        )
    else:
        return (
            f"❌ Decisión: NO LISTA. {passed}/{total} pasan.\n"
            f"   Necesita refinarse antes de intentar avanzar."
        )


def build_evidence_report(
    task_id: str,
    checks: dict,
    acceptance_criteria: list | None = None,
    quality_gates: list | None = None,
) -> str:
    """Construye un reporte de evidencia completo listo para el PO.

    Patrón Evidence + Meta-Instruction. Se inyecta en los USER GATES
    como la sección "QUÉ SUCEDIÓ / POR QUÉ" con evidencia cruda.

    Args:
        task_id: ID de la tarea.
        checks: dict con total, passed, passed_items, failed.
        acceptance_criteria: lista de dicts con criterion/passed.
        quality_gates: lista de dicts con gate/passed.

    Returns:
        Reporte formateado listo para presentar.
    """
    total = checks.get("total", 0)
    passed = checks.get("passed", 0)
    parts = [
        f"━━━ EVIDENCIA — {task_id} ({passed}/{total} checks) ━━━",
    ]

    ev = build_evidence_blocks(checks)
    if ev:
        parts.append(ev)

    if acceptance_criteria:
        parts.append(build_ac_section(acceptance_criteria))
    if quality_gates:
        parts.append(build_qg_section(quality_gates))

    has_critical = any(
        f.get("type") == "quality_gate" for f in checks.get("failed", [])
    )
    all_ac = (
        all(a["passed"] for a in acceptance_criteria) if acceptance_criteria else True
    )
    all_qg = all(q["passed"] for q in quality_gates) if quality_gates else True
    can_confirm = (
        passed >= total and not has_critical and all_ac and all_qg if total else False
    )

    parts.append(build_verdict(passed, total, checks.get("failed", []), can_confirm))

    # Meta-instruction: el agente debe presentar la evidencia al usuario
    parts.append(
        "\n🧠 META-INSTRUCTION: presenta la evidencia real de arriba al usuario "
        "antes de pedir su decisión. NO decidas por él."
    )
    return "\n".join(parts)


# ── Helpers de riesgo (labels) ───────────────────────────────────


def risk_label(value: float, thresholds: list[tuple[float, str]]) -> str:
    """Etiqueta de riesgo por umbrales: [(max, label), ...]."""
    for m, label in thresholds:
        if value <= m:
            return label
    return thresholds[-1][1]


def build_risk_summary(risks: dict) -> str:
    """Resume riesgos con labels legibles."""
    if not risks:
        return "  (sin riesgos)"
    curvature = risks.get("curvature", 0)
    pre_churn = risks.get("pre_churn", 0)
    trust = risks.get("commit_trust", 1)

    curvature_label = risk_label(
        curvature, [(0.2, "muy baja"), (0.3, "baja"), (0.4, "moderada"), (1.0, "alta")]
    )
    pre_churn_label = risk_label(
        pre_churn, [(0.15, "baja"), (0.25, "moderada"), (1.0, "alta")]
    )
    trust_label = risk_label(trust, [(0.7, "baja"), (0.85, "moderada"), (1.0, "alta")])
    risk_level = (
        "bajo"
        if (curvature + (1 - trust) + pre_churn) / 3 < 0.3
        else "moderado"
        if (curvature + (1 - trust) + pre_churn) / 3 < 0.5
        else "alto"
    )
    return (
        f"  • Curvatura: {curvature} ({curvature_label})\n"
        f"  • Pre-churn: {pre_churn} ({pre_churn_label})\n"
        f"  • Commit-trust: {trust} ({trust_label})\n"
        f"  • Nivel general: {risk_level}"
    )
