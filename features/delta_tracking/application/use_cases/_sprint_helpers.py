"""use_cases/_sprint_helpers.py — Helpers compartidos de los use cases del sprint.

Lógica de resolución de chain_dir/project_root/índice de spec y acceso al
SpringService del delta. PRINCIPIO: el DELTA (SpringService) es la unica
fuente del spring; plan.json no existe como fuente paralela (ADR-WALKER-013).

ADR-WALKER-048: estos helpers son IMPLEMENTACIÓN INTERNA del feature
delta_tracking. Nadie fuera del módulo debe importarlos.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone


def _spring_svc(sprint_dir: str):
    """Resuelve SpringService (delta) o levanta error claro.

    PRINCIPIO: el DELTA (SpringService) es la unica fuente del spring.
    No existe plan.json como fuente paralela (ADR-WALKER-013).
    """
    from features.delta_tracking import spring_service

    spring_svc = spring_service(chain_dir=sprint_dir)
    if spring_svc is None:
        raise ValueError(
            f"Spring no indexado en el delta para '{sprint_dir}'. "
            "Fuente unica: DeltaGraph (SpringService)."
        )
    return spring_svc


def _resolve_chain_dir(
    ctx, project_id: str = "", goal: str = ""
) -> str:
    """Resolve or create sprint chain directory.

    SSOT: Delegates to ProjectService for path resolution.
    Never uses dirname(dirname()) or hardcoded paths.
    Usa project_id de data cuando no hay sesion activa (CLI directo).

    PRINCIPIO: un chain_dir válido se detecta por .workflow-state.json
    (estado del motor). plan.json no existe como fuente paralela
    (ADR-WALKER-013): el estado de negocio vive en el delta.

    NAMING: los springs formales se nombran con un slug descriptivo del goal
    (sprint-{slug}, ej: sprint-workflow-contract-redesign). El nombre del
    directorio es el spring_id (get_spring_id deriva el id del basename).
    """
    cd = ctx.chain_dir or ""
    if (
        cd
        and os.path.isdir(cd)
        and os.path.exists(os.path.join(cd, ".workflow-state.json"))
    ):
        return cd

    from core.engine.bootstrap import resolve_service

    project_service = resolve_service("project_service")
    if project_service is None:
        return ""

    record = _get_active_record(ctx)
    pid = project_id or (getattr(record, "project_id", "") if record else "")

    if pid:
        active = project_service.find_active_sprint(pid)
        if active:
            return active
        # get_delta_dir ahora resuelve automaticamente desde el registry
        # (via get_project_dir -> _project_dir_from_registry)
        from shared.core.path_resolver import get_delta_dir

        delta_dir = str(get_delta_dir(pid))
        os.makedirs(delta_dir, exist_ok=True)
        sprint_name = _slugify_sprint_name(goal)
        sprint_dir = os.path.join(delta_dir, sprint_name)
        if not os.path.isdir(sprint_dir):
            os.makedirs(sprint_dir, exist_ok=True)
            return sprint_dir
        # Colisión de nombre (sprint del mismo slug): anexar timestamp
        sprint_dir = os.path.join(
            delta_dir,
            f"{sprint_name}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
        )
        os.makedirs(sprint_dir, exist_ok=True)
        return sprint_dir

    # Sin proyecto: directorio temporal (fallback solo bootstrap)
    import tempfile

    d = os.path.join(tempfile.gettempdir(), "walker", "sessions", ctx.session_id)
    os.makedirs(d, exist_ok=True)
    return d


def _get_active_record(ctx):
    from core.engine.domain.session import SessionStore

    try:
        return SessionStore.get_active()
    except Exception:
        return None


def _resolve_project_root(project_id: str) -> str:
    """Resuelve el directorio cognitivo del proyecto (.cognitive/projects/<id>)."""
    if not project_id:
        return ""
    from shared.core.path_resolver import get_project_dir

    try:
        return str(get_project_dir(project_id))
    except Exception:
        return ""


def _resolve_project_root_from_chain(sprint_dir: str) -> str:
    """Resuelve el project_root desde el chain_dir (misma fuente que
    SpringService.from_chain_dir / spring_service()).

    ANTES el fallback era dirname(dirname(sprint_dir)) = .cognitive/projects/
    <id>/data — UN nivel menos que el root real (.cognitive/projects/<id>).
    Con eso plan_sprint escribía el delta en cog/data/data/delta.json.gz
    (doble 'data') mientras spring_service(chain_dir) lee cog/data/delta.json.gz:
    dos CognitiveGraphs distintos → las specs creadas eran invisibles para
    el lector (smoke/status devolvían delta vacío).
    """
    if not sprint_dir:
        return ""
    from core.engine.bootstrap import resolve_service

    project_service = resolve_service("project_service")
    if project_service is None:
        return ""

    try:
        paths = project_service.resolve_paths(sprint_dir)
        root = getattr(paths, "project_root", "") or ""
        if root and os.path.isdir(root):
            return root
    except Exception:
        pass
    return os.path.dirname(os.path.dirname(os.path.dirname(sprint_dir)))


def _get_current_spec_index(sprint_dir: str) -> int:
    """Retorna el índice de la spec actual desde el DELTA (fuente unica).

    PRINCIPIO: el estado de negocio del spring vive en el nodo spring del
    delta (SpringService.get_spring_context). No se lee sprint-state.json.
    """
    try:
        from features.delta_tracking import spring_service

        spring_svc = spring_service(chain_dir=sprint_dir)
        if not spring_svc:
            return 0
        spring_id = spring_svc.get_spring_id(sprint_dir)
        spring = spring_svc.get_spring(spring_id)
        if not spring:
            return 0
        return int(
            spring.get(
                "current_spec_index",
                spring.get("current_task_index", 0),
            )
        )
    except Exception:
        return 0


def _get_current_spec_id(sprint_dir: str) -> str | None:
    """Retorna el spec_id de la última spec del delta (fuente unica).

    PRINCIPIO: el estado de negocio del spring vive en el delta. Esta función
    replica la resolución del side effect create_adr (misma semántica), para
    los use cases EDM (Approve/StartImplementation/StartCodeReview/Complete).
    """
    try:
        from features.delta_tracking import spring_service

        spring_svc = spring_service(chain_dir=sprint_dir)
        if spring_svc is None:
            return None
        spring_id = spring_svc.get_spring_id(sprint_dir)
        specs = spring_svc.get_specs_legacy(spring_id)
        if not specs:
            return None
        current = specs[-1] if isinstance(specs, list) else next(iter(specs.values()))
        if isinstance(current, dict):
            return current.get("spec_id")
        return getattr(current, "spec_id", None)
    except Exception:
        return None


def _slugify_sprint_name(goal: str) -> str:
    """Genera un nombre de spring formal desde el goal (sprint-{slug}).

    Reglas:
      - Toma la primera frase del goal (hasta ':' o ';').
      - Normaliza: lowercase, espacios->guiones, sin acentos/caracteres raros.
      - Recorta a 40 chars.
    Fallback: sprint-{timestamp} si el goal es vacío o no genera slug.
    Ej: 'Rediseno del contrato autoritativo del workflow: resolver gaps...'
        -> 'sprint-rediseno-contrato-autoritativo-wor'
    """
    import re
    import unicodedata

    text = (goal or "").strip()
    for sep in (":", ";"):
        if sep in text:
            text = text.split(sep, 1)[0]
            break
    if not text:
        return f"sprint-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    # Normalizar unicode (eliminar acentos) y quedarnos con alfanumericos
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    text = re.sub(r"-{2,}", "-", text)[:40].rstrip("-")
    if not text:
        return f"sprint-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    return f"sprint-{text}"
