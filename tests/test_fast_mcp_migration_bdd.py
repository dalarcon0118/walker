"""test_fast_mcp_migration_bdd.py — Capa de test de la migración a FastMCP.

Fase 1: EXACTAMENTE las 3 tools del workflow core (prepare/list/status),
VIVIENDO dentro de fast_mcp bajo la arquitectura §3bis (features + kernel +
composition). Sin walker_ping ni tools extra.

AUTONOMÍA (ADR-WALKER-071 §0 #5): fast_mcp NO importa NADA de `mcp/`
(ni app/, features/, shared/, protocols/) y NO pone mcp/ en sys.path.
La migración PORTA el código a fast_mcp/, no lo llama.
"""

from __future__ import annotations

import ast
import inspect

import pytest


def _remote_list(server) -> list:
    import asyncio

    return asyncio.run(server.list_tools())


@pytest.fixture(scope="module")
def fastmcp_server():
    from fast_mcp.server import build_server

    return build_server()


# ── Superficie ──────────────────────────────────────────────────────


def test_fast_mcp_paquete_importable():
    import fast_mcp
    from fast_mcp import server

    assert hasattr(server, "build_server")
    assert fast_mcp.__doc__


def test_build_server_construye_fastmcp(fastmcp_server):
    from mcp.server.fastmcp import FastMCP

    assert isinstance(fastmcp_server, FastMCP)


def test_server_expone_tools_fase1_y_fase2(fastmcp_server):
    """Las tools expuestas incluyen Fase 1 + Fase 2 + Fase 3.

    Fase 1: prepare/list/status. Fase 2: memorias CKG/CPG (14 tools).
    Fase 3: submit/transition/help/list_projects (workflow engine-backed).
    Sin walker_ping ni tools extra no declarados.
    """
    names = {t.name for t in _remote_list(fastmcp_server)}
    fase1 = {"prepare", "list", "status"}
    fase2 = {
        "build_code_index", "index_repo", "update_incremental_code",
        "extract_knowledge", "knowledge_query", "memory_schema",
        "recall_knowledge", "documenting_knowledge", "search_memories",
        "agent_context", "search_skills", "load_skill",
        "code_context", "inspect_codebase",
        "read_code", "code_search",
    }
    fase3 = {"submit", "advance", "transition", "help", "list_projects", "sprint_planning", "resolve_project_sprint", "workflow_state", "project_for_path", "trace_push", "trace_message", "implement_task", "run_tests", "verify_predicates", "capture"}
    expected = fase1 | fase2 | fase3
    assert names == expected, f"tools inesperadas: {names - expected}; faltantes: {expected - names}"


def test_list_roundtrip(fastmcp_server):
    import asyncio

    result = asyncio.run(fastmcp_server.call_tool("list", {}))
    text = None
    if isinstance(result, dict) and "content" in result:
        blocks = result["content"]
        if blocks and hasattr(blocks[0], "text"):
            text = blocks[0].text
    assert "projects" in str(text or result), f"list sin 'projects': {result}"


# ── Autonomía TOTAL (ADR §0 #5) — cero imports de mcp/ ─────────────


def test_fast_mcp_no_importa_nada_de_mcp():
    """Ningún módulo de fast_mcp importa app./features./shared./protocols./mcp/.

    La migración PORTA el código a fast_mcp/ (§0 #4): los casos de uso viven
    aquí; el único tercero permitido es la lib oficial mcp.server.fastmcp
    (+ pydantic), nunca el monorepo mcp/.
    """
    import fast_mcp.composition
    import fast_mcp.main
    import fast_mcp.registration
    import fast_mcp.server
    import fast_mcp.shared_kernel.paths
    import fast_mcp.phases.phase1_prepare
    import fast_mcp.features.project.application.use_cases
    import fast_mcp.shared_kernel.registry_store  # movido del feature al kernel (DIP, R1)
    import fast_mcp.features.project.infrastructure.layout
    import fast_mcp.features.project.infrastructure.detection

    forbidden_prefixes = ("app", "features", "shared", "protocols", "config", "data")
    allowed = {
        "mcp.server.fastmcp",
        "mcp.server.fastmcp.utilities.func_metadata",
        "mcp.server.fastmcp.tools.base",
        "pydantic",
        "returns",
    }

    def is_forbidden(module: str) -> bool:
        root = module.split(".")[0]
        if root in allowed:
            return False
        return root in forbidden_prefixes

    bad = set()
    for mod in (
        fast_mcp.server, fast_mcp.main, fast_mcp.composition,
        fast_mcp.registration, fast_mcp.shared_kernel.paths,
        fast_mcp.phases.phase1_prepare,
        fast_mcp.features.project.application.use_cases,
        fast_mcp.shared_kernel.registry_store,  # movido del feature al kernel (DIP, R1)
        fast_mcp.features.project.infrastructure.layout,
        fast_mcp.features.project.infrastructure.detection,
    ):
        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and is_forbidden(node.module):
                bad.add(node.module)
            elif isinstance(node, ast.Import):
                for n in node.names:
                    if is_forbidden(n.name):
                        bad.add(n.name)
    assert not bad, f"fast_mcp importa mcp/ (PROHIBIDO): {bad}"


def test_fast_mcp_runtime_cero_modulos_de_mcp():
    """Tras build + uso, sys.modules NO contiene app.*/features.*/shared.*/protocols.*."""
    import sys

    before = {m for m in sys.modules if m.split(".")[0] in ("app", "features", "shared", "protocols")}
    from fast_mcp import composition
    from fast_mcp.features.project.application import use_cases

    ctx = composition.build().project
    result = use_cases.list_use_case(ctx)

    after = {m for m in sys.modules if m.split(".")[0] in ("app", "features", "shared", "protocols")}
    nuevos = after - before
    assert not nuevos, f"fast_mcp cargó módulos de mcp/ en runtime: {sorted(nuevos)}"
    assert isinstance(result, dict)


# ── Arquitectura §3bis (composition) ───────────────────────────────


def test_composition_cablea_y_root_es_inyectable(tmp_path):
    """composition.build(root=...) construye contexts con raíz propia (tests)."""
    from fast_mcp import composition

    fake_root = tmp_path / "walker-root"
    (fake_root / "config").mkdir(parents=True)
    (fake_root / "config" / "config.json").write_text("{}")

    ctx = composition.build(root=fake_root).project
    # RegistryStore resuelve contra la raíz inyectada.
    assert str(ctx.store.read()) == "{}"
    registered = ctx.store.register("demo", "demo", str(tmp_path / "demo"))
    assert registered == fake_root / "data" / "projects.json"


# ── Fase 1 — contratos de las 3 tools ──────────────────────────────


def test_fase1_input_schema_es_declarativo(fastmcp_server):
    """El inputSchema servido al agente es el declarativo de la fase, no inferido."""
    tools = _remote_list(fastmcp_server)
    by_name = {t.name: t for t in tools}
    assert by_name["prepare"].inputSchema["properties"].get("path") == {"type": "string"}
    assert by_name["status"].inputSchema["properties"].get("_project_id") == {
        "type": "string"
    }


def test_fase1_prepare_crea_y_registra(tmp_path):
    """prepare crea el layout cognitivo + project.json y registra en data/projects.json."""
    import json

    from fast_mcp import composition
    from fast_mcp.features.project.application import use_cases

    repo = tmp_path / "demo-repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\n")

    fake_root = tmp_path / "walker-root"
    (fake_root / "config").mkdir(parents=True)
    (fake_root / "config" / "config.json").write_text("{}")
    ctx = composition.build(root=fake_root).project

    result = use_cases.prepare_use_case(ctx, project_path=str(repo))

    assert result["type"] == "prepare_result"
    assert result["project_id"] == "demo-repo"
    assert (repo / ".cognitive" / "projects" / "demo-repo" / "data" / "project.json").is_file()
    reg = json.loads((fake_root / "data" / "projects.json").read_text())
    assert reg["projects"]["demo-repo"]["repo_path"] == str(repo)

    # Idempotencia (mismo guard que legacy).
    again = use_cases.prepare_use_case(ctx, project_path=str(repo))
    assert again["type"] == "already_exists"


def test_fase1_status_resuelve_proyecto(tmp_path):
    """status resuelve project.json del registro y reporta health."""
    from fast_mcp import composition
    from fast_mcp.features.project.application import use_cases

    fake_root = tmp_path / "walker-root"
    (fake_root / "config").mkdir(parents=True)
    (fake_root / "config" / "config.json").write_text("{}")
    ctx = composition.build(root=fake_root).project

    repo = tmp_path / "demo-repo"
    use_cases.prepare_use_case(ctx, project_path=str(repo), args={"language": "python"})

    health = use_cases.status_use_case(ctx, {"_project_id": "demo-repo"})
    assert health["type"] == "project_health"
    assert health["checks"]["project_json"] == "ok"
    assert health["project"]["language"] == "python"
    assert health["project"]["repo_path"] == str(repo)


def test_fase1_watcher_status_autocontenido(tmp_path):
    """status reporta stale desde los archivos del propo watcher, sin CPG.

    El status stale se deriva de change_detected.json + .file_snapshot.json
    (persistidos por el FileWatcher), de forma auto-contenida — no depende del
    CPG ni de su fingerprint (Fase 2 pendiente resuelta para el status).
    """
    import json

    from fast_mcp import composition
    from fast_mcp.features.project.application import use_cases

    fake_root = tmp_path / "walker-root"
    (fake_root / "config").mkdir(parents=True)
    (fake_root / "config" / "config.json").write_text("{}")
    ctx = composition.build(root=fake_root).project

    repo = tmp_path / "demo-repo"
    use_cases.prepare_use_case(ctx, project_path=str(repo), args={"language": "python"})

    data_dir = repo / ".cognitive" / "projects" / "demo-repo" / "data"
    (data_dir / "change_detected.json").write_text(
        json.dumps(
            {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "files_changed": ["a.py", "b.py"],
                "total_changes": 2,
                "new_changes": ["b.py"],
                "fingerprint": "abc",
                "ckg_status": "up_to_date",
                "cpg_status": "stale",
            }
        )
    )
    (data_dir / ".file_snapshot.json").write_text(
        json.dumps({"a.py": "1:1", "b.py": "2:2"})
    )

    # Direto: helper autocontenido.
    w = use_cases._watcher_status(data_dir)
    assert w["available"] is True
    assert w["total_changes"] == 2
    assert w["files_changed"] == ["a.py", "b.py"]
    assert w["cpg_status"] == "stale"
    assert w["stale"] is True
    assert w["snapshot_files"] == 2

    # Vía status_use_case: el estado del watcher viaja en el health.
    health = use_cases.status_use_case(ctx, {"_project_id": "demo-repo"})
    assert health["checks"]["watcher"] == "ok"
    assert health["watcher"]["stale"] is True
    assert health["watcher"]["total_changes"] == 2


def test_fase1_watcher_status_pending_sin_archivos(tmp_path):
    """Sin archivos del watcher, status reporta pending (no simulado)."""
    from fast_mcp import composition
    from fast_mcp.features.project.application import use_cases

    fake_root = tmp_path / "walker-root"
    (fake_root / "config").mkdir(parents=True)
    (fake_root / "config" / "config.json").write_text("{}")
    ctx = composition.build(root=fake_root).project

    repo = tmp_path / "demo-repo"
    use_cases.prepare_use_case(ctx, project_path=str(repo), args={"language": "python"})

    health = use_cases.status_use_case(ctx, {"_project_id": "demo-repo"})
    assert health["checks"]["watcher"] == "pending"
    assert health["watcher"]["available"] is False
    assert health["watcher"]["cpg_status"] == "pending"
    assert health["watcher"]["stale"] is False


def test_cpg_index_se_registra_con_register_cpg_indexes(tmp_path):
    """register_cpg_indexes registra el indice 'cpg' y permite store_many.

    ADR-068 (una feature, un indice): el indice CPG es SSOT del schema.py.
    Sin registrarlo, store_many/replace_blob("cpg", ...) fallan con "Indice
    'cpg' no registrado" y el CPG queda vacio → el watcher nunca detecta stale.
    """
    from fast_mcp.shared_kernel.cognitive_graph import build_cognitive_graph
    from fast_mcp.features.code_analysis.schema import register_cpg_indexes

    g = build_cognitive_graph(str(tmp_path), register_defaults=register_cpg_indexes)

    # El indice queda registrado (get_index no lanza).
    idx = g.get_index("cpg")
    assert idx is not None

    # store_many sobre el indice registrado NO falla (antes: no registrado).
    result = g.store_many(
        "cpg",
        {"n1": {"name": "foo", "path": "a.py", "type": "function"}},
    )
    assert result.get("stored", 0) >= 1

    # search devuelve el doc indexado.
    hits = g.search("cpg", {"query": "foo"})
    assert hits.get("hits", {}).get("total", 0) >= 1


def test_cpg_repo_default_graph_registra_indice(tmp_path):
    """CognitiveCpgRepository (via _default_graph) registra el indice 'cpg'.

    El repo es el dueño del puerto StorageBackend y el punto donde build()
    ejecuta self._repo.save() → replace_blob("cpg", ...). Si su grafo no
    registra el indice, el build falla con "Indice 'cpg' no registrado"
    ANTES de _sync_to_cognitive. Este test cubre ese camino.
    """
    from fast_mcp.features.code_analysis.infrastructure.cognitive_cpg_repo import (
        CognitiveCpgRepository,
    )

    repo = CognitiveCpgRepository(str(tmp_path), "demo")
    # replace_blob sobre el índice registrado NO debe lanzar StorageContractError.
    repo.save(
        {
            "project_id": "demo",
            "nodes": {"n1": {"name": "foo", "path": "a.py", "type": "function"}},
            "edges": [{"type": "CALLS", "from": "n1", "to": "n2"}],
            "stats": {"total_nodes": 1},
        }
    )
    data = repo.load("demo")
    assert "n1" in data.get("nodes", {})
    assert repo.exists() is True


def test_indice_cpg_se_persiste_y_restaura_al_reabrir(tmp_path):
    """El catálogo de índices sobrevive al reabrir la BD (opción 2).

    Boot del escenario MCP real: una instancia construye el repo (registra +
    persiste el índice 'cpg'); una SEGUNDA instancia sobre el MISMO data_dir
    reabre la BD y el índice ya está disponible SIN volver a registrarlo —
    no depende de que cada caller recuerde pasar register_defaults.
    """
    from fast_mcp.shared_kernel.cognitive_graph import build_cognitive_graph

    data_dir = str(tmp_path / "mem")
    # Resolver la conexión real del data_dir para ambas instancias.
    from fast_mcp.shared_kernel.cognitive_graph.config import (
        load_memory_connection,
    )
    from fast_mcp.shared_kernel.cognitive_graph.backends import (
        resolve_storage_backend,
    )

    conn = load_memory_connection(data_dir)

    # Instancia 1: registra el índice (lo persiste).
    from fast_mcp.features.code_analysis.schema import register_cpg_indexes

    g1 = build_cognitive_graph(
        chain_dir="", conn=conn, backend=resolve_storage_backend(conn),
        register_defaults=register_cpg_indexes,
    )
    g1.store_many(
        "cpg",
        {"n1": {"name": "foo", "path": "a.py", "type": "function"}},
    )
    assert "cpg" in g1.list_indexes()

    # Instancia 2: nueva conexión + backend, MISMO data_dir, SIN llamar a
    # register_cpg_indexes. El índice debe restaurarse desde la BD.
    conn2 = load_memory_connection(data_dir)
    g2 = build_cognitive_graph(
        chain_dir="", conn=conn2, backend=resolve_storage_backend(conn2),
    )
    assert "cpg" in g2.list_indexes(), (
        "El índice 'cpg' debió restaurarse del catálogo persistido en la BD"
    )
    # Y debe poder leer los docs escritos por la instancia 1.
    hits = g2.search("cpg", {"query": "foo"})
    assert hits.get("hits", {}).get("total", 0) >= 1


def test_resolve_paths_data_dir_canonico_cuando_existe_layout(tmp_path):
    """resolve_paths produce el data_dir canónico .cognitive/projects/<pid>/data.

    ADR-WALKER-054 D4: el layout cognitivo ES la fuente de verdad de las
    memorias. ANTES resolve_paths priorizaba el legacy <repo>/data, lo que
    desalineaba CPG/CKG/grep/read del data_dir que usan prepare/status/watcher.
    """
    from fast_mcp import composition
    from fast_mcp.features.project.application import use_cases
    from fast_mcp.shared_kernel.project_support import ProjectService

    fake_root = tmp_path / "walker-root"
    (fake_root / "config").mkdir(parents=True)
    (fake_root / "config" / "config.json").write_text("{}")
    ctx = composition.build(root=fake_root).project

    repo = tmp_path / "demo-repo"
    (repo / "data").mkdir(parents=True)  # legacy <repo>/data existe (índice del bug)
    use_cases.prepare_use_case(ctx, project_path=str(repo), args={"language": "python"})

    cog_data = repo / ".cognitive" / "projects" / "demo-repo" / "data"
    paths = ProjectService.resolve_paths(str(repo))
    assert paths.data_dir == str(cog_data), (
        "El data_dir debe ser el layout cognitivo canónico, no el legacy <repo>/data"
    )


def test_memory_context_cpg_usa_mismo_data_dir_que_context(tmp_path):
    """ctx.cpg() construye el CpgService con el data_dir del context (no repo root).

    ADR-WALKER-054 D4: cpg debe usar el MISMO data_dir canónico que ckg/status/
    watcher. ANTES ctx.cpg() re-resolvía por from_chain_dir → data_dir=project_root
    (base parásita en el repo root).
    """
    from fast_mcp import composition
    from fast_mcp.features.project.application import use_cases
    from fast_mcp.memories.context import MemoryContext

    fake_root = tmp_path / "walker-root"
    (fake_root / "config").mkdir(parents=True)
    (fake_root / "config" / "config.json").write_text("{}")
    ctx = composition.build(root=fake_root).project

    repo = tmp_path / "demo-repo"
    use_cases.prepare_use_case(ctx, project_path=str(repo), args={"language": "python"})

    cog_data = repo / ".cognitive" / "projects" / "demo-repo" / "data"
    mctx = MemoryContext.resolve(str(repo))
    assert mctx is not None
    # El context ya resuelve el data_dir canónico.
    assert mctx.data_dir == str(cog_data)

    cpg = mctx.cpg()
    assert cpg is not None
    # El CpgService debe usar el data_dir del context (canónico), no el repo root.
    assert cpg._repo._data_dir == str(cog_data), (
        "El repo del CPG debe apuntar al data_dir canónico, no al project_root"
    )