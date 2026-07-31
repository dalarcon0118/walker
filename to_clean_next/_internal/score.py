#!/usr/bin/env python3
"""delta-score.py — Calcula el score de una tarea usando la fórmula FASE 5.

Uso:
    python3 delta-score.py <chain_dir> [task_id]

Si se omite task_id, calcula score para todas las tareas pendientes.

Formula:
    Δ_base = primary_predicates / total_predicates
    Δ_ajustado = Δ_base * importer_factor
    Δ_ponderado = Δ_ajustado * work_type_multiplier
    score = min(0.6·Δ_ponderado + 0.3·confidence − 0.1·risk + coverage_bonus, 1.0)

Exit code:
    0 → cálculo exitoso
    2 → error
"""

import json
import os
import sys


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"❌ ERROR: {e}", file=sys.stderr)
        sys.exit(2)


WORK_TYPE_MULTIPLIER = {
    "refactor_code_motion": 1.25,
    "refactor": 1.0,
    "feature_new_method": 1.0,
    "feature_new_file": 0.85,
    "feature_new_bc": 0.70,
    "refactor_inline": 0.65,
    "bugfix": 1.0,
    "optimization": 0.80,
    "docs": 1.0,
    "test": 0.85,
}

# Map work_type → confidence base
WORK_TYPE_CONFIDENCE = {
    "refactor_code_motion": 0.95,
    "refactor": 0.80,
    "feature_new_method": 0.75,
    "feature_new_file": 0.70,
    "feature_new_bc": 0.50,
    "refactor_inline": 0.65,
    "bugfix": 0.85,
    "optimization": 0.75,
    "docs": 0.90,
    "test": 0.90,
}

# ═══════════════════════════════════════════════════════════════
#  SCRUM ROLE ADJUSTMENT (FASE 5 + Roles)
# ═══════════════════════════════════════════════════════════════
# Cada rol tiene distinto peso en el score:
#   po (Product Owner) — define, no codea → bajo riesgo, alto impacto
#   dev (Developer) — implementa → riesgo técnico normal
#   qa (Quality Assurance) — valida → máxima confianza, gate de calidad
#
# El multiplicador afecta delta_ponderado.
# La confianza afecta el componente 0.3·confidence de la fórmula.

SCRUM_ROLE_MULTIPLIER = {
    "po": 0.90,  # Define, no codea — bajo riesgo de bugs
    "dev": 0.75,  # Implementa — riesgo técnico normal
    "qa": 0.95,  # Valida — máxima confianza
}

SCRUM_ROLE_CONFIDENCE = {
    "po": 0.90,
    "dev": 0.75,
    "qa": 0.95,
}


def calculate_importer_factor(importer_files: int) -> float:
    if importer_files <= 2:
        return 1.0
    elif importer_files <= 5:
        return 0.85
    else:
        return 0.70


def calculate_score(
    primary_predicates: int,
    total_predicates: int,
    importer_files: int,
    work_type: str,
    risk: float,
    has_tests: bool,
    custom_confidence: float | None = None,
    scrum_role: str = "dev",
) -> dict:
    """Calculate score for a single task using the FASE 5 formula.

    Incorpora ajustes por rol Scrum:
    - po: define, no codea → bajo riesgo (mult 0.90)
    - dev: implementa → riesgo técnico normal (mult 0.75)
    - qa: valida → máxima confianza (mult 0.95)

    Returns dict with all intermediate values and final score.
    """
    if total_predicates == 0:
        return {"error": "total_predicates is 0", "score": 0.0}

    # Paso 1: Δ base
    delta_base = primary_predicates / total_predicates

    # Paso 2: Ajustar por importers
    importer_factor = calculate_importer_factor(importer_files)
    delta_ajustado = delta_base * importer_factor

    # Paso 3: Work type multiplier
    wt_multiplier = WORK_TYPE_MULTIPLIER.get(work_type, 1.0)
    delta_ponderado = delta_ajustado * wt_multiplier

    # Paso 3b: Scrum role multiplier (ajuste adicional)
    sr_multiplier = SCRUM_ROLE_MULTIPLIER.get(scrum_role, 0.75)
    delta_ponderado *= sr_multiplier

    # Paso 4: Score final
    # La confianza base se ajusta por rol: QA tiene más confianza
    # (valida), Dev menos (implementa código nuevo)
    role_confidence = SCRUM_ROLE_CONFIDENCE.get(scrum_role)
    if custom_confidence is not None:
        confidence = custom_confidence
    elif role_confidence is not None:
        # Blend: 70% work_type confidence + 30% role confidence
        wt_conf = WORK_TYPE_CONFIDENCE.get(work_type, 0.70)
        confidence = 0.7 * wt_conf + 0.3 * role_confidence
    else:
        confidence = WORK_TYPE_CONFIDENCE.get(work_type, 0.70)

    coverage_bonus = 0.05 if has_tests else 0.0
    score = min(
        0.6 * delta_ponderado + 0.3 * confidence - 0.1 * risk + coverage_bonus,
        1.0,
    )

    return {
        "delta_base": round(delta_base, 4),
        "importer_factor": importer_factor,
        "delta_ajustado": round(delta_ajustado, 4),
        "work_type_multiplier": wt_multiplier,
        "scrum_role_multiplier": sr_multiplier,
        "delta_ponderado": round(delta_ponderado, 4),
        "confidence": round(confidence, 2),
        "scrum_role": scrum_role,
        "risk": round(risk, 2),
        "coverage_bonus": coverage_bonus,
        "score": round(max(score, 0.0), 2),
    }


def main():
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("Uso: python3 delta-score.py <chain_dir> [task_id]")
        print("")
        print("Calcula score FASE 5 para tareas en un plan delta.")
        print("")
        print("Argumentos:")
        print("  chain_dir    Ruta al directorio del chain (contiene plan.json)")
        print(
            "  task_id      Opcional. Si se omite, calcula todas las tareas pendientes."
        )
        sys.exit(2)

    chain_dir = sys.argv[1]
    target_task_id = sys.argv[2] if len(sys.argv) > 2 else None

    plan_path = os.path.join(chain_dir, "plan.json")
    progress_path = os.path.join(chain_dir, "progress.json")

    plan = load_json(plan_path)
    pl = plan.get("plan", plan)

    # Extract project_id (multi-project support)
    project_id = None
    try:
        project_id = pl.get("project", {}).get("project_id") or None
    except Exception:
        pass

    progress = load_json(progress_path)
    prog = progress.get("progress", progress)

    # Count total predicates
    goal = pl.get("goal", {})
    predicates = goal.get("predicates", {})
    total_predicates = sum(
        [
            len(predicates.get("must_exist", [])),
            len(predicates.get("must_not_exist", [])),
            len(predicates.get("invariants", [])),
        ]
    )

    if total_predicates == 0:
        print("  ⚠️  No hay predicates en goal.predicates — usando score por defecto")
        print(
            "  ℹ️  Define predicates en --predicates de spring.py create para obtener score real."
        )
        total_predicates = 1  # Evita division por cero, scores serán default

    task_states = prog.get("current", {}).get("task_states", {})
    tasks = pl.get("tasks", [])

    results = []

    for task in tasks:
        tid = task["task_id"]
        if target_task_id and tid != target_task_id:
            continue
        if task_states.get(tid) == "completed":
            continue

        primary = len(task.get("validation", {}).get("predicates", []))
        importers = task.get("importer_files", 0)
        wt = task.get("work_type", "refactor")
        rsk = task.get("risks", {}).get("curvature", 0.20)
        tests = bool(task.get("impacted_artifacts", {}).get("tests", []))
        sr = task.get("scrum_role", "dev")

        result = calculate_score(
            primary_predicates=primary,
            total_predicates=total_predicates,
            importer_files=importers,
            work_type=wt,
            risk=rsk,
            has_tests=tests,
            scrum_role=sr,
        )
        result["task_id"] = tid
        result["objective"] = task.get("objective", "")[:60]
        result["role"] = task.get("role", "core")
        result["scrum_role"] = sr
        results.append(result)

    if not results:
        print("ℹ️  No hay tareas pendientes para calcular score.")
        sys.exit(0)

    # ── Self-sufficient output ──────────────────────────────────
    print("=" * 70)
    chain_label = os.path.basename(chain_dir)
    project_label = f" — {project_id}" if project_id else ""
    print(f"📊 DELTA-SCORE: FASE 5 — {chain_label}{project_label}")
    print("=" * 70)
    print(f"📋 Total predicates: {total_predicates}")
    print()

    # Sort by score descending
    results_sorted = sorted(results, key=lambda r: r["score"], reverse=True)
    best = results_sorted[0] if results_sorted else None

    for r in results:
        status = "🟢" if r["score"] >= 0.40 else "🟡" if r["score"] >= 0.20 else "🔴"
        sr_label = r.get("scrum_role", r["role"])
        print(f"  {status} {r['task_id']} (rol={sr_label}): score {r['score']}")
        print(f"     {r['objective']}")
        scrum_mult = r.get("scrum_role_multiplier", 1.0)
        print(
            f"     Δ_base={r['delta_base']} × importers={r['importer_factor']} × wt={r['work_type_multiplier']} × role={scrum_mult}"
        )
        print(
            f"     confidence={r['confidence']} − risk={r['risk']} + bonus={r['coverage_bonus']}"
        )
        print(
            f"     Fórmula: 0.6·{r['delta_ponderado']} + 0.3·{r['confidence']} − 0.1·{r['risk']} + {r['coverage_bonus']} = {r['score']}"
        )
        print()

    # Decision protocol
    print("─" * 70)
    print("📋 DECISION PROTOCOL — priorización de tareas:")
    print()
    if best:
        print(f"   🥇 Mayor score: {best['task_id']} ({best['score']})")
        print(f"      {best['objective']}")
        print()
        if best["score"] >= 0.40:
            print("   ▶️  Prioritario — ejecuta esta tarea primero:")
        elif best["score"] >= 0.20:
            print("   ▶️  Prioridad media — puede ejecutarse después de las críticas:")
        else:
            print("   ▶️  Baja prioridad — considera si es necesario ahora:")
        print(f"      python3 scripts/delta-advance.py {chain_dir} {best['task_id']}")

        if len(results_sorted) > 1:
            print()
            print("   Orden sugerido:")
            for i, r in enumerate(results_sorted, 1):
                print(
                    f"      {i}. {r['task_id']} (score: {r['score']}) — {r['objective'][:60]}"
                )
    print()
    print("💡 El score es la prioridad. Ejecuta las tareas de mayor score primero.")
    print("=" * 70)

    # Update next_action scores if progress.json exists
    if os.path.exists(progress_path) and target_task_id:
        next_action = prog.get("current", {}).get("next_action", {})
        if next_action and isinstance(next_action, dict):
            for r in results:
                if r["task_id"] == next_action.get("task_id"):
                    next_action["score"] = r["score"]
                    next_action["confidence"] = r["confidence"]
                    next_action["risk"] = r["risk"]
                    next_action["delta"] = round(
                        r["delta_base"] * r["importer_factor"], 4
                    )
                    # Add integrity checksum before writing
                    try:
                        from _internal.progress_checksum import add_checksum

                        add_checksum(progress)
                    except ImportError:
                        sys.stderr.write(
                            "⚠️  progress_checksum no disponible — escribiendo progress.json sin checksum\n"
                        )
                    # Update model parameters if they exist
                    with open(progress_path, "w") as f:
                        json.dump(progress, f, indent=2, ensure_ascii=False)
                    print(
                        f"   📝 progress.json actualizado con score={r['score']} para {target_task_id}"
                    )
                    break
