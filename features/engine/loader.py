"""
loader.py — Carga de workflow y estado desde disco.

SRP: Unica responsabilidad = leer/interpretar/recuperar workflow.json y sprint-state.json.
Extraido de engine.py para separar IO del resto de la logica.

PRINCIPIO: NO hay fallbacks a workflow legacy hardcoded. El workflow del motor
se carga SIEMPRE desde la plantilla workflow.v3 canónica. Si falta, ERROR.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from libs.infrastructure.utils import (
    load_json,
    state_path,
    save_state,
    _DONE_STATUSES,
    _INIT_SCRIPT,
    _resolve_task_id,
)
from libs.path_resolver import (
    get_engine_workflow_dir,
    get_engine_workflow_path,
    resolve_project_root,
)
from .plan_editor import _load_rejected_tasks


def find_workflow(chain_dir: str | None = None) -> dict:
    """Carga el workflow del motor (SSOT): proyecto o plantilla canónica.

    Orden:
      1. workflow del proyecto (workflow.v3 o workflow.json) si existe
      2. plantilla canónica del motor (workflow.v3)
      3. ERROR — nunca workflow hardcoded.

    Raises:
        FileNotFoundError: si no hay workflow del proyecto ni plantilla del motor.
    """
    # 1. Workflow del proyecto
    if chain_dir:
        try:
            from features.project import ProjectService

            paths = ProjectService.resolve_paths(chain_dir)
            project_wf = paths.workflow_json_path
            if project_wf and os.path.exists(project_wf):
                data = load_json(project_wf)
                if data:
                    return data
        except Exception:
            pass

    # 2. Plantilla canónica del motor (SSOT)
    engine_wf = get_engine_workflow_path()
    if os.path.exists(engine_wf):
        data = load_json(str(engine_wf))
        if data:
            return data

    # 3. ERROR — no hay fallback hardcoded
    raise FileNotFoundError(
        f"No se encontró workflow. Proyecto: "
        f"{paths.workflow_json_path if chain_dir else '(sin chain_dir)'}. "
        f"Plantilla del motor: {engine_wf}"
    )


def ensure_state(chain_dir: str | None = None) -> dict:
    """Lee el estado del workflow desde sprint-state.json / .workflow-state.json.

    PRINCIPIO: no reconstruye estado inventado ni retorna defaults efímeros.
    Si no hay estado persistido, retorna dict vacío (el estado se crea
    al iniciar el sprint de verdad).
    """
    if not chain_dir:
        return {}
    sp = state_path(chain_dir)
    if sp and os.path.exists(sp):
        state = load_json(sp)
        if state:
            if not state.get("current_node") and state.get("walker"):
                state = dict(state.get("walker", {}))
            return state
    return {}
