"""cli/project.py — Descubrimiento de proyectos y sprints.

Responsabilidad: localizar proyectos, sprints activos, mostrar dashboards.
Separado de los comandos CLI para que la lógica de discovery sea
independiente del formato de salida.
"""

from __future__ import annotations
import json
import os

from features.engine.domain.session import SessionRecord, SessionStore
from libs.path_resolver import (
    get_projects_dir,
    get_project_dir,
    get_delta_dir,
    get_sprint_dir,
    get_manifest_path,
    read_project_manifest,
)


def _find_sprint(project_id: str, sprint_hint: str = "") -> str | None:
    """Busca un chain_dir dentro de un proyecto.

    Si no hay sprint_hint, busca primero el sprint activo,
    luego el mas reciente.
    """
    delta_dir = get_delta_dir(project_id)
    if not delta_dir.is_dir():
        return None
    sprints = sorted(
        (entry.name for entry in delta_dir.iterdir()),
        reverse=True,
    )

    if sprint_hint:
        for s in sprints:
            spath = get_sprint_dir(project_id, s)
            if s.startswith(sprint_hint) and (spath / "sprint-state.json").exists():
                return str(spath)
        return None

    # Preferir sprint activo
    for s in sprints:
        spath = get_sprint_dir(project_id, s)
        state_file = spath / "sprint-state.json"
        if state_file.exists():
            try:
                with open(str(state_file)) as f:
                    state_data = json.load(f)
                if state_data.get("sprint", {}).get("status") == "active":
                    return str(spath)
            except Exception:
                pass

    # Fallback: ultimo sprint con estado
    for s in sprints:
        spath = get_sprint_dir(project_id, s)
        if (spath / "sprint-state.json").exists():
            return str(spath)
    return None


def _list_projects() -> list[dict]:
    """Lista todos los proyectos con sus sprints."""
    pd = get_projects_dir()
    if not pd.is_dir():
        return []
    projects = []
    for pdir_entry in sorted(pd.iterdir()):
        pid = pdir_entry.name
        try:
            pdir = get_project_dir(pid)
            if not pdir.is_dir():
                continue
            delta_dir_path = get_delta_dir(pid)
            if not delta_dir_path.is_dir():
                if get_manifest_path(pid).exists():
                    projects.append({"project_id": pid, "sprints": []})
                continue
            sprints = []
            for s in sorted(
                (entry.name for entry in delta_dir_path.iterdir()),
                reverse=True,
            ):
                spath = get_sprint_dir(pid, s)
                if not spath.is_dir():
                    continue
                state_file = spath / "sprint-state.json"
                if state_file.exists():
                    try:
                        with open(str(state_file)) as f:
                            st = json.load(f)
                        status = st.get("sprint", {}).get("status", "unknown")
                    except Exception:
                        status = "unknown"
                    sprints.append({"id": s, "status": status, "chain_dir": str(spath)})
                else:
                    sprints.append(
                        {"id": s, "status": "no-state", "chain_dir": str(spath)}
                    )
            projects.append({"project_id": pid, "sprints": sprints})
        except FileNotFoundError:
            continue  # Skip projects not fully migrated
    return projects


def _read_project_description(project_id: str) -> str:
    """Lee la descripción de un proyecto desde su project.json."""
    manifest = read_project_manifest(project_id)
    if not manifest:
        return ""
    identity = manifest.get("project", manifest).get("identity", {})
    return identity.get("description", "")


def _switch_to_project(project_id: str) -> int:
    """Cambia la sesión activa a un proyecto directamente (sin START node)."""
    projects = _list_projects()
    for p in projects:
        if p["project_id"] != project_id:
            continue
        active = [s for s in p["sprints"] if s["status"] == "active"]
        if active:
            sprint_id = active[0]["id"]
            chain_dir = active[0]["chain_dir"]
        else:
            sprint_id = ""
            chain_dir = ""

        record = SessionRecord.create(project_id=project_id, chain_dir=chain_dir)
        record.ensure_session_dir()
        SessionStore.save(record)
        SessionStore.set_active(record.session_id)

        if active:
            print(f"  {project_id} — active sprint: {sprint_id}")
            print(f"   Session: {record.session_id}")
        else:
            print(f"  {project_id} — no active sprint")
            print(f"   Session: {record.session_id}")
            print(
                "   Create a sprint: walker3 {}/task/create_first_sprint".format(
                    project_id
                )
            )
        return 0

    print(f"Project not found: {project_id}")
    return 1


def _show_projects_dashboard(projects: list[dict]) -> int:
    """Show multi-tenant dashboard with all projects and active sprints."""
    session_id = os.environ.get("WALKER_SESSION_ID", "")
    active_project = ""
    if session_id:
        record = SessionStore.load(session_id)
        if record:
            active_project = record.project_id or ""

    if not projects:
        print(" No projects in", str(get_projects_dir()))
        print()
        print("  To create one, use:")
        print("    walker3 switch <project>/<sprint>   — select existing project")
        print("    or create a directory in .cognitive/projects/<name>/")
        return 0

    print(" WALKER — Multi-Tenant Dashboard")
    print("=" * 60)
    print(f"  Active session: {session_id or '(none)'}")
    print()

    for p in projects:
        pid = p["project_id"]
        marker = "  " if pid == active_project else ""
        if not p["sprints"]:
            print(f"   {pid}{marker} (no sprints)")
            continue

        active_sprints = [s for s in p["sprints"] if s["status"] == "active"]
        total = len(p["sprints"])
        active_count = len(active_sprints)
        status_icon = (
            ""
            if active_count > 0
            else ""
            if all(s["status"] == "closed" for s in p["sprints"])
            else ""
        )
        print(f"   {status_icon} {pid}{marker}")
        print(f"       Sprints: {total}  |  Active: {active_count}")

        for s in p["sprints"][:3]:
            if s["status"] == "active":
                icon = ""
            elif s["status"] == "closed":
                icon = ""
            else:
                icon = ""
            print(f"       {icon} {s['id'][:55]}  [{s['status']}]")

        if len(p["sprints"]) > 3:
            print(
                f"       ... y {len(p['sprints']) - 3} más (usa 'walker3 {pid}/list')"
            )
        print()

    print("  Comandos útiles:")
    print("    walker3 <project>/status        — Detailed project status")
    print("    walker3 <project>/list           — List sprints of a project")
    print("    walker3 <project>/<sprint>/status — Status of a specific sprint")
    print("    walker3 switch <project>/<sprint> — Switch active session")
    return 0


def _show_quality_report(pid: str) -> None:
    """Muestra el quality report persistido junto al CPG (si existe).

    El reporte refleja las métricas de calidad + gates + recomendaciones del
    estado ACTUAL del código, generado automáticamente al indexar (build/index).
    Así el agente/usuario decide si refactorizar antes o indexar con métricas.
    """
    try:
        from libs.path_resolver import get_data_path

        qr_path = get_data_path(pid, "quality_report.json")
    except Exception:
        return
    if not qr_path.exists():
        return
    try:
        with open(qr_path) as f:
            qr = json.load(f)
    except (json.JSONDecodeError, OSError):
        return

    if qr.get("degraded"):
        print("\n  ⚠️  QUALITY REPORT (degradado en el index):")
        print(f"     {str(qr.get('error', ''))[:120]}")
        return

    passed = qr.get("passed", True)
    icon = "✅" if passed else "❌"
    m = qr.get("metrics", {}) or {}
    print(f"\n  {icon} QUALITY: {qr.get('summary', '')}")
    print(
        f"     📊 files={m.get('files', 0)} loc={m.get('loc', 0)} "
        f"funcs={m.get('functions', 0)} cyclo_max={m.get('cyclo_max', 0)} "
        f"dead_code={m.get('dead_code', 0)}"
    )
    for g in qr.get("gates", []):
        mark = "✅" if g.get("passed") else "❌"
        print(
            f"     {mark} {g.get('name', '?')} = {g.get('actual', '?')} "
            f"(max {g.get('threshold', '?')})"
        )

    # Bug proposals post-index (si existen)
    bp_path = get_data_path(pid, "bug_proposals.json")
    if bp_path.exists():
        try:
            with open(bp_path) as f:
                bp = json.load(f)
            total = bp.get("total_findings", 0)
            if total:
                print(f"     🐛 Bug proposals: {total} hallazgos")
                for fi in bp.get("findings", [])[:5]:
                    sev = fi.get("severity", "")
                    sev_tag = f"[{sev.upper()}]" if sev else ""
                    print(
                        f"        {sev_tag} {str(fi.get('label', fi.get('title', 'hallazgo')))[:90]}"
                    )
        except (json.JSONDecodeError, OSError):
            pass

    recs = qr.get("recommendations", [])
    if recs:
        print("     💡 Recomendaciones:")
        for r in recs[:3]:
            print(f"        • [{r.get('area', '?')}] {str(r.get('action', ''))[:110]}")


def _show_project_status(pid: str) -> int:
    """Muestra el estado de un proyecto con guía de setup gradual."""
    from bootstrap.cli.utils import _get_api, _output, _ensure_qlty_for_project
    from libs.path_resolver import get_repo_path, get_cpg_path

    projects = _list_projects()
    for p in projects:
        if p["project_id"] != pid:
            continue

        # ── 1. Check repo_path ──
        repo = get_repo_path(pid)
        if not repo or not os.path.isdir(repo):
            print(f"\n  🛠  Project '{pid}' not configured.")
            print("  No repo_path to find the code.")
            print("\n  ▶  Configure the project:")
            print(f"       cd /path/to/your/code/{pid}")
            print(f"       walker prepare --name {pid}")
            print("  Or edit repo_path in:")
            print(f"       {get_manifest_path(pid)}")
            return 0

        # Only initialize qlty if repo_path exists (and it's a git repo)
        pdir = str(get_project_dir(pid))
        _ensure_qlty_for_project(pdir)

        # ── 2. Check CPG (code scanned) ──
        cpg_path = get_cpg_path(pid)
        has_cpg = cpg_path.exists()
        if not has_cpg:
            print(f"\n  📄 Project '{pid}' — code not scanned yet.")
            print(f"  Code at: {repo}")
            print("\n  ▶  Scan the code to build the CPG:")
            print(f"       walker agents/kilocode/{pid}/task/scan_code_and_build_cpg")
            print("\n  Then you can:")
            print(f"       walker agents/kilocode/{pid}/task/build_initial_ckg")
            return 0

        # ── 3. Quality report (métricas + gates persistidos en el index) ──
        _show_quality_report(pid)

        # ── 4. Check CKG (knowledge base) ──
        from libs.path_resolver import get_ckg_path

        ckg_path = get_ckg_path(pid)
        has_ckg = ckg_path.exists()
        if not has_ckg:
            print(f"\n  🧠 Project '{pid}' — CPG ready, no CKG yet.")
            print(f"  CPG: {cpg_path}")
            print("\n  ▶  Build the initial knowledge graph:")
            print(f"       walker agents/kilocode/{pid}/task/build_initial_ckg")
            print("\n  This will scan your code for libraries, classes,")
            print("  functions, and conventions.")
            return 0

        # ── 4. Check sprints ──
        if not p["sprints"]:
            print(f"\n  🚀 Project '{pid}' — setup complete.")
            print(f"  CPG: {cpg_path}")
            print(f"  CKG: {ckg_path}")
            print("\n  ▶  Create the first sprint:")
            print(f"       walker3 {pid}/task/create_first_sprint")
            return 0

        # ── 4. Show active sprint status ──
        print(f"  {pid}")
        print(f"  Sprints: {len(p['sprints'])}")
        print()
        active = [s for s in p["sprints"] if s["status"] == "active"]
        if active:
            sprint = active[0]
            print(f"   Active sprint: {sprint['id']}")
            print("  Full status:")
            api = _get_api(pid, sprint["id"])
            _output(api.status())
            return 0

        print("  No active sprint. Latest sprints:")
        for s in p["sprints"][:5]:
            icon = "" if s["status"] == "closed" else ""
            print(f"    {icon} {s['id'][:55]}  [{s['status']}]")
        print()
        print(f"  Use: walker3 {pid}/list  to see all sprints")
        return 0

    print(f"Project not found: {pid}")
    return 1


def _show_human_dashboard(auto_select: str = "") -> int:
    """Show projects with description or auto-select one."""
    projects = _list_projects()
    if not projects:
        print("No projects. Create one with 'walker3 init'.")
        return 0

    if auto_select:
        return _switch_to_project(auto_select)

    for i, p in enumerate(projects, 1):
        pid = p["project_id"]
        desc = _read_project_description(pid)
        if desc and len(desc) > 120:
            desc = desc[:117] + "..."
        active = [s for s in p["sprints"] if s["status"] == "active"]
        icon = "" if active else " "
        print(f"  {i}. {icon} {pid}")
        if desc:
            print(f"     {desc}")

    print()
    print("Options:")
    print("  1-5  Select a project")
    print("  n    Create a new project")
    return 0
