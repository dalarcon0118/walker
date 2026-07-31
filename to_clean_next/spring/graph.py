"""spring/graph.py — Graph visualization"""

from __future__ import annotations

import json
import os
from .common import eprint


def cmd_graph(args: list[str]) -> int:
    """DeltaGraph: consultas de grafo sobre datos del sprint.

    Subcommands:
        neighbors <node>        Nodos adyacentes con tipo de edge
        path <from> <to>       Camino más corto entre dos nodos
        query <filter>         Filtro por tipo/status
        export [--html]        Exportar grafo (JSON por defecto)

    Uso:
        spring.py graph neighbors TASK-005
        spring.py graph path TASK-005 spring.py
        spring.py graph query "type=task,status=pending"
        spring.py graph export --html
    """

    if len(args) < 2:
        _print_graph_help()
        return 2

    chain_dir = os.path.abspath(args[0])
    sub = args[1]

    if not os.path.isdir(chain_dir):
        eprint(f"❌ Directorio no encontrado: {chain_dir}")
        return 2

    if sub == "neighbors":
        return _graph_neighbors(chain_dir, args[2:])
    elif sub == "path":
        return _graph_path(chain_dir, args[2:])
    elif sub == "query":
        return _graph_query(chain_dir, args[2:])
    elif sub == "export":
        return _graph_export(chain_dir, args[2:])
    elif sub == "critical":
        return _graph_critical(chain_dir)
    elif sub == "stats":
        return _graph_stats(chain_dir)
    else:
        eprint(f"❌ Subcomando graph desconocido: {sub}")
        _print_graph_help()
        return 2


def _graph_neighbors(chain_dir: str, sub_args: list[str]) -> int:
    from _internal.graph import DeltaGraph

    if len(sub_args) < 1:
        eprint("Uso: spring.py graph <chain_dir> neighbors <node_id> [--edge <type>]")
        return 2

    node_id = sub_args[0]
    edge_filter = None

    for i, a in enumerate(sub_args):
        if a == "--edge" and i + 1 < len(sub_args):
            edge_filter = sub_args[i + 1]
            break

    g = DeltaGraph(chain_dir)
    results = g.neighbors(node_id, edge_filter)

    if not results:
        print(f"(sin adyacentes para {node_id})")
        return 0

    print(f"  Vecinos de {node_id}:")
    for r in results:
        status_icon = {"completed": "✅", "pending": "⬜", "done": "✅"}.get(
            r.get("status", ""), ""
        )
        print(f"  {status_icon} {r['label'][:50]:50s} [{r['type']}] ← {r['edge']}")
    print(f"  Total: {len(results)} vecinos")
    return 0


def _graph_query(chain_dir: str, sub_args: list[str]) -> int:
    from _internal.graph import DeltaGraph

    filter_str = " ".join(sub_args) if sub_args else ""

    # Parse filter: type=task,status=pending
    node_types = None
    status_filter = None
    for part in filter_str.split(","):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            if k == "type":
                node_types = [t.strip() for t in v.split("|")]
            elif k == "status":
                status_filter = v.strip()

    g = DeltaGraph(chain_dir)
    results = g.subgraph(node_types=node_types, status=status_filter)

    if not results:
        print(f"(sin resultados para: {filter_str})")
        return 0

    for r in results[:50]:  # max 50
        status_icon = {"completed": "✅", "pending": "⬜"}.get(r.get("status", ""), "")
        print(f"  {status_icon} {r['label'][:50]:50s} [{r['type']}] deg={r['degree']}")
    if len(results) > 50:
        print(f"  ... (+{len(results) - 50} más)")
    print(f"  Total: {len(results)} nodos")
    return 0


def _write_graph_html(data: dict, chain_dir: str):
    """Genera HTML interactivo desde template externo."""
    nodes_json = json.dumps(data["nodes"])
    edges_json = json.dumps(data["edges"])
    stats = data["stats"]

    template_path = os.path.join(_SCRIPT_DIR, "_internal", "templates", "graph.html")
    if not os.path.exists(template_path):
        eprint(f"❌ Template no encontrado: {template_path}")
        eprint(
            "   Crea _internal/templates/graph.html con placeholders $TITLE, $BODY, etc."
        )
        return

    import string

    with open(template_path) as f:
        template = string.Template(f.read())

    html = template.safe_substitute(
        CHAIN_ID=data.get("chain_id", "delta-graph"),
        NODES=str(stats["nodes"]),
        EDGES=str(stats["edges"]),
        NODES_JSON=nodes_json,
        EDGES_JSON=edges_json,
    )

    out_path = os.path.join(chain_dir, "graph.html")
    with open(out_path, "w") as f:
        f.write(html)
    print(f"📝 Grafo HTML exportado: {out_path}")
    print(f"   Nodos: {stats['nodes']}, Edges: {stats['edges']}")
