#!/usr/bin/env python3
"""prompt_engine.py — Motor de prompts con patrón Evidence + Meta-Instruction.

Genera bloques de prompt auto-contextualizados donde cada predicate
incluye evidencia cruda (stdout, grep lines, exit codes) + assessment
+ meta-instruction para que el LLM presente los datos al usuario.

Las cadenas de texto están en templates/*.txt, separadas del código.

Uso:
    prompt = build_review_prompt(task, loop_data, chain_dir, task_id)

    print(build_close_evidence(plan, project_root))
"""

from __future__ import annotations

import os
from string import Template


# ── Template loader ─────────────────────────────────────────────


def _render(template_name: str, **data) -> str:
    """Load a template from templates/ and substitute $vars."""
    tpl_dir = os.path.join(os.path.dirname(__file__), "templates")
    tpl_path = os.path.join(tpl_dir, template_name)
    if not os.path.exists(tpl_path):
        return f"[template {template_name} not found]"
    with open(tpl_path) as f:
        return Template(f.read()).safe_substitute(**data)


# ── Detail formatting (legacy, used by build_review_prompt) ──────


def _format_detail_block(detail: dict) -> str:
    """Formatear un bloque de evidencia cruda desde el info dict de verify_predicate.

    El detail dict contiene lo que verification.py devuelve en su info:
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


def _format_predicate_block(
    predicate_text: str,
    passed: bool,
    detail: dict,
    index: int,
    total: int,
) -> str:
    """Formatear un bloque completo de predicate: [evidencia] → [assessment]."""
    status_icon = "✅" if passed else "❌"
    status_text = "CUMPLE" if passed else "NO CUMPLE"
    label = detail.get("label", str(predicate_text)[:70])
    method = detail.get("method", "?")

    lines = [
        f"  [{index}/{total}] {status_icon} {label}",
        f"       Método: {method}",
    ]

    evidence = _format_detail_block(detail)
    if evidence:
        lines.append(evidence)

    lines.append(f"       → {status_icon} {status_text}")
    lines.append("")
    return "\n".join(lines)


# ── Section builders para build_review_prompt ────────────────────


def _build_evidence_blocks(checks: dict) -> str:
    """Build evidence sections for passed and failed predicates."""
    parts = []
    passed = checks.get("passed_items", [])
    if passed:
        blocks = ["", "─" * 70, "✅ EVIDENCIA — Predicates que CUMPLEN", "─" * 70, ""]
        for i, p in enumerate(passed, 1):
            blocks.append(
                _format_predicate_block(
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
                _format_predicate_block(
                    f.get("predicate", ""), False, f.get("detail", {}), i, len(failed)
                )
            )
        parts.append("\n".join(blocks))
    return "\n".join(parts)


def _build_ac_section(ac_results: list) -> str:
    """Build acceptance criteria section."""
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


def _build_qg_section(qg_results: list) -> str:
    """Build quality gates section."""
    if not qg_results:
        return ""
    passed = sum(1 for qg in qg_results if qg["passed"])
    lines = ["", "─" * 70, f"🔒 QUALITY GATES: {passed}/{len(qg_results)}", "─" * 70]
    for qg in qg_results:
        icon = "✅" if qg["passed"] else "❌"
        lines.append(f"  {icon} {qg['gate'][:80]}")
    return "\n".join(lines)


# ── build_review_prompt (task advance) ──────────────────────────


def build_review_prompt(
    task: dict,
    loop_data: dict,
    chain_dir: str | None = None,
    task_id: str | None = None,
) -> str:
    """Construir prompt de revisión con Evidence + Meta-Instruction.

    Genera las secciones dinámicas en código e inyecta los resultados
    en el template ``review_prompt.txt`` (ver templates/).

    Args:
        task: Dict de la tarea desde plan.json.
        loop_data: Dict con resultados del TASK LOOP (checks, acceptance, quality).
        chain_dir: Ruta al sprint (para comandos de siguiente paso).
        task_id: ID de la tarea (fallback si task no tiene task_id).

    Returns:
        Prompt listo para mostrar al LLM, con evidencia cruda por predicate.
    """
    tid = task.get("task_id", task_id or "?")
    checks = loop_data["checks"]
    total = checks["total"]
    passed = checks["passed"]
    coverage_pct = round(passed / max(total, 1) * 100)

    # ── Evidence blocks ───────────────────────────────────────
    _build_evidence_blocks(checks)

    # ── Acceptance criteria section ────────────────────────────
    _build_ac_section(loop_data.get("acceptance_criteria", []))

    # ── Quality gates section ──────────────────────────────────
    _build_qg_section(loop_data.get("quality_gates", []))

    # ── Assessment text ────────────────────────────────────────
    has_critical = any(f.get("type") == "quality_gate" for f in checks["failed"])
    all_ac_passed = all(ac["passed"] for ac in ac_results) if ac_results else True
    all_qg_passed = all(qg["passed"] for qg in qg_results) if qg_results else True

    if coverage_pct >= 90 and not has_critical and all_ac_passed and all_qg_passed:
        pass
    elif coverage_pct < 80:
        pass
    elif has_critical:
        pass
    elif not all_ac_passed:
        pass
    elif coverage_pct < 90:
        pass
    else:
        pass

    # ── Action section ─────────────────────────────────────────
    can_confirm = (
        coverage_pct >= 90 and not has_critical and all_ac_passed and all_qg_passed
    )
    if can_confirm:
        pass
    else:
        pass

    # ── Generar líneas de verificación, decisión y comando ──
    risks = task.get("risks", {})
    files = task.get("impacted_artifacts", {}).get("files", [])

    v_lines = []
    for item in passed_items:
        label = item.get("predicate", "")
        detail = item.get("detail", {})
        method = detail.get("method", "?")
        symb = detail.get("symbol", "") or ""
        fhint = detail.get("file_hint") or detail.get("file", "") or ""
        if method in ("ast-grep", "symbol_exists") and symb:
            loc = f" en {os.path.basename(fhint)}" if fhint else ""
            v_lines.append(f"  ✅ {symb}{loc}")
        else:
            v_lines.append(f"  ✅ {label[:70]}")
    for item in failed_items:
        v_lines.append(f"  ❌ {item.get('predicate', '')[:70]}")
    verification_lines = "\n".join(v_lines) if v_lines else "  (ninguna)"

    if not failed_items:
        decision = "✅ TASK COMPLETA — Puedes aprobar"
        command = (
            f"task.py advance {chain_dir} {tid} --confirm"
            if chain_dir and tid
            else "task.py advance <chain> <task> --confirm"
        )
    elif passed > 0:
        decision = "⚠️  AVANCE PARCIAL — Revisa fallos antes de aprobar"
        command = (
            f"task.py advance {chain_dir} {tid}"
            if chain_dir and tid
            else "task.py advance <chain> <task>"
        )
    else:
        decision = "❌ NO LISTA — Necesita refinarse"
        command = (
            f"task.py research {chain_dir} {tid}"
            if chain_dir and tid
            else "task.py research <chain> <task>"
        )

    tc = task.get("task_context", {})
    files = task.get("impacted_artifacts", {}).get("files", [])
    risks = task.get("risks", {})

    curvature = risks.get("curvature", 0)
    pre_churn = risks.get("pre_churn", 0)
    trust = risks.get("commit_trust", 1)

    def _rl(val, thresholds):
        for m, l in thresholds:
            if val <= m:
                return l
        return thresholds[-1][1]

    curvature_label = _rl(
        curvature, [(0.2, "muy baja"), (0.3, "baja"), (0.4, "moderada"), (1.0, "alta")]
    )
    pre_churn_label = _rl(
        pre_churn, [(0.15, "baja"), (0.25, "moderada"), (1.0, "alta")]
    )
    trust_label = _rl(trust, [(0.7, "baja"), (0.85, "moderada"), (1.0, "alta")])
    risk_level = (
        "bajo"
        if (curvature + (1 - trust) + pre_churn) / 3 < 0.3
        else "moderado"
        if (curvature + (1 - trust) + pre_churn) / 3 < 0.5
        else "alto"
    )

    def ctx(k):
        return tc.get(k, "")[:200] or "(sin datos)"

    def _j(v):
        return ", ".join(str(x)[:80] for x in v) if v else "ninguna"

    confirm_cmd = (
        f"task.py advance {chain_dir} {tid} --confirm"
        if chain_dir and tid
        else "task.py advance <chain> <task> --confirm"
    )
    reject_cmd = (
        f"task.py research {chain_dir} {tid}"
        if chain_dir and tid
        else "task.py research <chain> <task>"
    )

    return _render(
        "review_prompt.txt",
        task_id=tid,
        description=task.get("task_description", "")[:100],
        objective=task.get("objective", "")[:200],
        result_state=task.get("result_state", "")[:200],
        type=task.get("type", "?"),
        domain=task.get("domain", "?"),
        role=task.get("role", "?"),
        scrum_role=task.get("scrum_role", "?"),
        work_type=task.get("work_type", "refactor"),
        importer_files=str(task.get("importer_files", 0)),
        depends_on=_j(task.get("depends_on", [])),
        preconditions=_j(task.get("preconditions", [])),
        acceptance_criteria=_j(task.get("acceptance_criteria", [])),
        quality_gates=_j(task.get("quality_gates", [])),
        loop_verification=str(len(task.get("loop_verification", []))) + " items",
        loop_acceptance=task.get("loop_acceptance", "all"),
        decisions=_j(task.get("decisions", [])),
        refinement=str(task.get("refinement", "") or "{}"),
        file_count=len(files),
        files="  ".join(files) if files else "(ninguno)",
        curvature=curvature,
        curvature_label=curvature_label,
        pre_churn=pre_churn,
        pre_churn_label=pre_churn_label,
        trust=trust,
        trust_label=trust_label,
        risk_level=risk_level,
        context_obs=ctx("observation"),
        context_est=ctx("structure"),
        context_comp=ctx("behavior"),
        context_causal=ctx("causality"),
        context_final=ctx("purpose"),
        context_princ=ctx("principles"),
        verification_lines=verification_lines,
        passed=passed,
        total=total,
        decision=decision,
        command=command,
        command_confirm=confirm_cmd,
        command_reject=reject_cmd,
    )


# ── build_close_evidence (spring.py close dashboard) ─────────────


def build_close_evidence(plan: dict, project_root: str) -> str:
    """Dashboard de evidencia para spring.py close.

    Aplana los predicates del goal, verifica cada uno contra código
    real con verify_predicates_batch (cache + batch ast-grep),
    y renderiza el template close_evidence.txt.

    Args:
        plan: plan.json completo.
        project_root: Ruta absoluta a la raíz del proyecto.

    Returns:
        String formateado listo para imprimir.
    """
    from _verify_engine import verify_predicates_batch

    pl = plan.get("plan", plan)
    preds = pl.get("goal", {}).get("predicates", {})

    # Aplanar predicates
    flat_predicates: list[object] = []
    flat_ptypes: list[str] = []
    for ptype in ("must_exist", "must_not_exist", "invariants"):
        for p in preds.get(ptype, []):
            flat_predicates.append(p)
            flat_ptypes.append(ptype)

    if not flat_predicates:
        return "  (sin predicates en goal)\n"

    # Verificar en batch (1 sg call por lenguaje para symbol_exists)
    try:
        batch_results = verify_predicates_batch(flat_predicates, project_root)
    except Exception:
        batch_results = [
            (
                False,
                {
                    "method": "error",
                    "error": "verify_predicates_batch crash",
                    "label": str(p),
                },
            )
            for p in flat_predicates
        ]

    rows: list[str] = []
    passed = 0

    for idx, (ptype, predicate) in enumerate(zip(flat_ptypes, flat_predicates)):
        ok, info = batch_results[idx]
        method = info.get("method", "?")[:14]
        label = info.get("label", str(predicate))[:55]
        icon = "✅" if ok else "❌"
        rows.append(f"  {icon} [{idx + 1:2d}] {method:<15s} {label}")
        if ok:
            passed += 1

    total = len(flat)
    pct = round(passed / max(total, 1) * 100)

    if pct >= 90:
        assessment = "🥇 Assessment: cobertura excelente — puedes cerrar el sprint"
    elif pct >= 70:
        assessment = (
            f"⚠️  Assessment: cobertura moderada ({pct}%) — revisa antes de cerrar"
        )
    else:
        assessment = (
            f"❌ Assessment: cobertura insuficiente ({pct}%) — sprint NO cerrable"
        )

    return _render(
        "close_evidence.txt",
        total=total,
        predicate_rows="\n".join(rows),
        passed=passed,
        pct=f"{pct}",
        assessment=assessment,
    )


def build_status_evidence(plan: dict, project_root: str) -> str:
    """Evidence dashboard por predicate para spring.py status --verify.

    Aplana los validation.predicates de todas las tareas del plan,
    verifica cada uno contra código real con verify_predicates_batch
    (cache + batch ast-grep), y renderiza el template status_evidence.txt.

    NOTA: Los predicates ``command_output`` o ``command_exit`` cuyo comando
    ejecuta ``spring.py``, ``task.py`` o ``delta-advance.py`` se muestran
    como ``⏭️  [SKIP]`` en lugar de verificarse. Esto evita recursión
    (el dashboard se llamaría a sí mismo en un subproceso infinito).

    Args:
        plan: plan.json completo.
        project_root: Ruta absoluta a la raíz del proyecto.

    Returns:
        String formateado listo para imprimir, o cadena vacía
        si no hay predicates.
    """
    from _verify_engine import verify_predicates_batch

    pl = plan.get("plan", plan)
    tasks = pl.get("tasks", [])

    # Aplanar todos los predicates de todas las tareas
    flat_predicates: list[object] = []
    flat_task_ids: list[str] = []
    for t in tasks:
        tid = t.get("task_id", "?")
        for p in t.get("validation", {}).get("predicates", []):
            flat_predicates.append(p)
            flat_task_ids.append(tid)

    if not flat_predicates:
        return "  (no hay predicates en las tareas)\n"

    # ── Separar predicates que causan recursión ──
    to_verify: list[object] = []
    to_verify_idx: list[int] = []
    skip_count = 0
    skip_rows: list[tuple[str, str]] = []  # (tid, label)

    for idx, (tid, p) in enumerate(zip(flat_task_ids, flat_predicates)):
        command = _get_command_from_predicate(p)
        if command and _is_recursive_command(command):
            skip_rows.append((tid, command[:50]))
            skip_count += 1
        else:
            to_verify.append(p)
            to_verify_idx.append(idx)

    # ── Verificar solo predicates no-recursivos ──
    if to_verify:
        try:
            batch_results_subset = verify_predicates_batch(to_verify, project_root)
        except Exception:
            batch_results_subset = [
                (False, {"method": "error", "error": "crash", "label": str(p)})
                for p in to_verify
            ]
    else:
        batch_results_subset = []

    # Reconstruir resultados en orden original
    result_map: dict[int, tuple[bool, dict]] = {}
    for sub_idx, orig_idx in enumerate(to_verify_idx):
        result_map[orig_idx] = batch_results_subset[sub_idx]

    rows: list[str] = []
    passed = 0

    for idx, (tid, predicate) in enumerate(zip(flat_task_ids, flat_predicates)):
        if idx in result_map:
            ok, info = result_map[idx]
            method = info.get("method", "?")[:14]
            label = info.get("label", str(predicate))[:50]
            icon = "✅" if ok else "❌"
            rows.append(f"  {icon} [{tid:<8}] {method:<15s} {label}")
            if ok:
                passed += 1
        else:
            # Skip rows from skip_rows
            for stid, slab in skip_rows:
                if stid == tid and slab == _get_command_from_predicate(predicate)[:50]:
                    rows.append(f"  ⏭️  [{tid:<8}] {'(integration)':<15s} {slab}")
                    break

    total = len(flat_predicates) - skip_count
    pct = round(passed / max(total, 1) * 100)

    return _render(
        "status_evidence.txt",
        evidence_rows="\n".join(rows),
        passed=passed,
        total=total,
        pct=f"{pct}",
    )


# ── Review template builders ──────────────────────────────────


def _build_header(
    task: dict, risks: dict, files: list, deps: list, passed: int, total: int, pct: int
) -> str:
    """Cabecera tipo Jira: ID, tipo, estado, riesgo, archivos."""
    tid = task.get("task_id", "?")
    ttype = task.get("type", "feature")
    desc = task.get("task_description", "")[:80]
    r_curv = risks.get("curvature", "?")
    r_trust = risks.get("commit_trust", "?")

    return (
        f"[{tid}] {ttype}: {desc}\n"
        f"  Cobertura: {passed}/{total} ({pct}%)"
        f" | Riesgo: curvature={r_curv}, trust={r_trust}"
        f" | Archivos: {len(files)}"
    )


def _build_body(
    task: dict,
    tc: dict,
    risks: dict,
    files: list,
    passed_items: list,
    failed_items: list,
) -> str:
    """Cuerpo: contexto, verificaciones, archivos."""
    lines = []
    task.get("type", "feature")
    task.get("task_description", "")[:80]
    task.get("objective", "")[:120]
    task.get("result_state", "")[:120]

    # ── Contexto desde task_context (6 capas) ──
    if tc:
        obs = tc.get("observation", "")[:200]
        est = tc.get("structure", "")[:200]
        if obs or est:
            lines.append("📋 Contexto:")
            if obs:
                lines.append(f"  {obs}")
            if est:
                lines.append(f"  {est}")

    # ── Archivos ──
    if files:
        lines.append(f"\n📄 Archivos ({len(files)}):")
        for f in files:
            lines.append(f"  • {f}")

    # ── Riesgos ──
    if risks:
        lines.append("\n⚠️  Riesgos:")
        for k, v in risks.items():
            lines.append(f"  • {k}: {v}")

    # ── Verificaciones ──
    if passed_items:
        lines.append(f"\n✅ Verificaciones ({len(passed_items)}):")
        for item in passed_items:
            label = item.get("predicate", "")
            detail = item.get("detail", {})
            method = detail.get("method", "?")
            symb = detail.get("symbol", "") or ""
            fhint = detail.get("file_hint") or detail.get("file", "") or ""
            if method in ("ast-grep", "symbol_exists") and symb and fhint:
                lines.append(f"  • {symb} — presente en {os.path.basename(fhint)}")
            elif method in ("ast-grep", "symbol_exists") and symb:
                lines.append(f"  • {symb} — presente en el código")
            else:
                lines.append(f"  • {label[:70]}")

    if failed_items:
        lines.append(f"\n❌ Fallos ({len(failed_items)}):")
        for item in failed_items:
            lines.append(f"  • {item.get('predicate', '')[:70]}")

    return "\n".join(lines)


def _build_veredict(
    passed: int,
    total: int,
    failed_items: list,
    chain_dir: str | None,
    task_id: str | None,
    can_confirm: bool,
) -> str:
    """Veredicto final: decisión + comando."""
    if can_confirm:
        cmd = (
            f"python3 scripts/task.py advance {chain_dir} {task_id} --confirm"
            if chain_dir and task_id
            else "python3 scripts/task.py advance <chain> <task> --confirm"
        )
        return (
            f"✅ Decisión: TASK COMPLETA. {passed}/{total} verificaciones superan.\n"
            f"   → {cmd}"
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


_RECURSIVE_TOOLS = ("spring.py", "task.py", "delta-advance.py")


def _get_command_from_predicate(predicate: object) -> str | None:
    """Extrae el comando de un predicate si es de tipo command_output/exit."""
    if isinstance(predicate, dict):
        cmd = predicate.get("command", "")
        if cmd:
            return cmd
    if isinstance(predicate, str):
        return predicate
    return None


def _is_recursive_command(command: str) -> bool:
    """True si el comando ejecuta directamente spring.py/task.py/delta-advance.py.

    Detecta el tool como SCRIPT PRINCIPAL (no como argumento).
    Ej: ``python3 spring.py status`` → True
        ``python3 verify_symbol.py spring.py cmd_create`` → False (spring.py es argumento)
    """
    words = command.split()
    for i, w in enumerate(words):
        # Busca patrones: "python3 spring.py" o "./spring.py" o "spring.py" directo
        for tool in _RECURSIVE_TOOLS:
            if tool in w:
                # Solo es recursivo si el tool es el script PRINCIPAL
                # (no aparece como argumento de otro script o comando)
                prev_word = words[i - 1] if i > 0 else ""
                # Si prev_word es python, zsh, sh, etc → es script principal
                # Si no hay prev_word → es script principal directo
                # Si prev_word es otra cosa → probablemente es argumento
                if i == 0 or prev_word in ("python3", "python", "bash", "sh", "zsh"):
                    return True
                # Si el tool aparece DESPUÉS de -c, es argumento (no recursivo)
                if prev_word == "-c":
                    return False
    return False
