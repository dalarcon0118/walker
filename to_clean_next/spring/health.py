"""spring/health.py — Sprint health"""

from __future__ import annotations

import os
from .common import eprint, load_json


def cmd_health(args: list[str]) -> int:
    """Muestra score de salud del sprint (TASK-035).

    Calcula score 0-100 basado en:
    - Cobertura de tasks completadas vs total
    - Riesgo acumulado (curvatura media)
    - Trazabilidad (predicates con archivo asociado)
    - Acoplamiento (grado medio del grafo)
    - Progreso general

    Uso: spring.py health <chain_dir>
    """
    if len(args) < 1:
        eprint("Uso: spring.py health <chain_dir>")
        return 2

    chain_dir = os.path.abspath(args[0])
    plan_path = os.path.join(chain_dir, "plan.json")
    sprint_state_path = os.path.join(chain_dir, "sprint-state.json")

    if not os.path.exists(plan_path) or not os.path.exists(sprint_state_path):
        eprint("❌ plan.json o sprint-state.json no encontrados")
        return 2

    plan = load_json(plan_path)
    progress = load_json(sprint_state_path)
    if not plan or not progress:
        return 2

    pl = plan.get("plan", plan)

    # ── SSOT: task statuses desde progress.current.task_states ──
    progress_task_states = (
        progress.get("progress", {}).get("current", {}).get("task_states", {})
    )
    if progress_task_states:
        task_statuses = {
            tid: v.get("status", v) if isinstance(v, dict) else v
            for tid, v in progress_task_states.items()
        }
    else:
        # Fallback: top-level tasks (dict or list)
        ts_fallback = progress.get("tasks", {})
        if isinstance(ts_fallback, list):
            task_statuses = {tid: "pending" for tid in ts_fallback}
        elif isinstance(ts_fallback, dict):
            task_statuses = {
                k: v.get("status", "pending") if isinstance(v, dict) else v
                for k, v in ts_fallback.items()
            }
        else:
            task_statuses = {}

    tasks = pl.get("tasks", [])

    total = max(len(tasks), len(task_statuses)) or 1
    completed = sum(1 for s in task_statuses.values() if s == "completed")
    progreso = completed / total * 100

    # 1. Cobertura de tasks
    cobertura = progreso

    # 2. Riesgo acumulado
    curvatures = [t.get("risks", {}).get("curvature", 0) for t in tasks]
    avg_curvature = (sum(curvatures) / len(curvatures)) if curvatures else 0
    riesgo = max(100 - (avg_curvature * 100), 0)

    # 3. Trazabilidad y acoplamiento via grafo
    trazabilidad = 0
    acoplamiento = 0
    try:
        from _internal.graph import DeltaGraph

        g = DeltaGraph(chain_dir)
        data = g.export_data()
        data.get("stats", {}).get("nodes", 0)
        # Trazabilidad: predicates que referencian archivos
        sum(
            1
            for n in data.get("nodes", [])
            if n.get("type") == "pred" and n.get("label", "")
        )
        sum(1 for n in data.get("nodes", []) if n.get("type") == "file")
        total_node_types = {}
        for n in data.get("nodes", []):
            total_node_types[n.get("type", "?")] = (
                total_node_types.get(n.get("type", "?"), 0) + 1
            )

        # Acoplamiento: edges por nodo (densidad)
        total_edges = data.get("stats", {}).get("edges", 0)
        total_nodes = data.get("stats", {}).get("nodes", 1)
        densidad = total_edges / total_nodes if total_nodes > 0 else 0
        acoplamiento = min(densidad * 20, 100)  # Normalizar

        # Trazabilidad: predicates con archivo / total predicates
        pred_count = total_node_types.get("pred", 0) or 1
        file_count = total_node_types.get("file", 0)
        trazabilidad = min(file_count / pred_count * 200, 100)

        # Predicates totales desde grafo
        total_preds_val = pred_count
    except Exception:
        total_preds_val = 0

    # 4. Score compuesto (0-100)
    score = round(
        cobertura * 0.25 + riesgo * 0.25 + trazabilidad * 0.25 + acoplamiento * 0.25, 1
    )

    # Nivel de salud
    if score >= 80:
        nivel = "🟢 Excelente"
    elif score >= 60:
        nivel = "🟡 Bueno"
    elif score >= 40:
        nivel = "🟠 Regular"
    else:
        nivel = "🔴 Crítico"

    print(f"\nHealth Score: {score}/100 ({nivel})")
    print("=" * 50)
    print(f"  📊 Progreso:     {completed}/{total} tasks ({progreso:.0f}%)")
    print(f"  📈 Cobertura:    {cobertura:.0f}/100")
    print(f"  ⚠️  Riesgo:       {riesgo:.0f}/100 (curvatura media: {avg_curvature:.2f})")
    print(
        f"  🔗 Acoplamiento: {acoplamiento:.0f}/100 (densidad: {densidad:.2f})"
        if "densidad" in dir()
        else ""
    )
    print(f"  📋 Trazabilidad: {trazabilidad:.0f}/100")
    print(f"  📝 Predicates:   {total_preds_val} en el grafo")
    print()
    return 0
