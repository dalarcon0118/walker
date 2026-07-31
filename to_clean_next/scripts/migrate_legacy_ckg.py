#!/usr/bin/env python3
"""
migrate_legacy_ckg.py — Migra el CKG legacy al formato actual.

Lee _archive/legacy/ckg.json (v1, 180 nodos + 96 edges) y los
convierte al formato actual de data/ckg.json.gz (v3, nodes + edges).

Uso:
    python3 migrate_legacy_ckg.py                          # proyecto delta-skill por defecto
    python3 migrate_legacy_ckg.py --project react-native-frontend
    python3 migrate_legacy_ckg.py --project cognitive-mcp
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from datetime import datetime, timezone


def find_project_dir(base: str, project_id: str) -> str:
    """Busca el directorio del proyecto dentro de .cognitive/projects/."""
    # Intentar ruta directa
    direct = os.path.join(base, ".cognitive", "projects", project_id)
    if os.path.isdir(direct):
        return direct
    # Intentar ruta absoluta
    alt = os.path.join(base, project_id)
    if os.path.isdir(alt) and os.path.exists(os.path.join(alt, "project.json")):
        return alt
    raise FileNotFoundError(
        f"Proyecto '{project_id}' no encontrado en {base}/.cognitive/projects/"
    )


def find_legacy_file(project_dir: str) -> str | None:
    """Busca el archivo legacy ckg.json."""
    candidates = [
        os.path.join(project_dir, "_archive", "legacy", "ckg.json"),
        os.path.join(project_dir, "data", "ckg_legacy.json"),
        os.path.join(project_dir, "ckg.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    # Escanear _archive recursivamente
    archive_dir = os.path.join(project_dir, "_archive")
    if os.path.isdir(archive_dir):
        for root, dirs, files in os.walk(archive_dir):
            for f in files:
                if f == "ckg.json":
                    return os.path.join(root, f)
    return None


def find_current_file(project_dir: str) -> str:
    """Ruta al archivo ckg.json.gz actual."""
    return os.path.join(project_dir, "data", "ckg.json.gz")


def load_legacy(path: str) -> dict:
    """Carga el archivo legacy ckg.json."""
    with open(path, "rt") as f:
        return json.load(f)


def load_current(path: str) -> dict:
    """Carga el ckg.json.gz actual."""
    if os.path.exists(path):
        try:
            with gzip.open(path, "rt") as f:
                return json.load(f)
        except Exception as e:
            print(f"  ⚠️  Error cargando {path}: {e}")
    return {"version": "3.0", "generated_at": "", "nodes": [], "edges": []}


def save_current(path: str, data: dict) -> None:
    """Guarda el ckg.json.gz."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  ✅ Guardado: {path} ({os.path.getsize(path)} bytes)")


def convert_node(nid: str, legacy_node: dict) -> dict:
    """Convierte un nodo legacy al formato actual."""
    node_type = legacy_node.get("type", "unknown")

    new_node = {
        "id": nid,
        "type": node_type,
        "statement": legacy_node.get("label", legacy_node.get("statement", "")),
        "rationale": legacy_node.get("description", legacy_node.get("rationale", "")),
        "scope": legacy_node.get("scope", []),
        "supersedes": legacy_node.get("supersedes", []),
        "metadata": legacy_node.get("metadata", {}),
        "tags": legacy_node.get("tags", []),
        "context": legacy_node.get("context", ""),
        "location": legacy_node.get("location", ""),
        "api": legacy_node.get("api", ""),
        "author": legacy_node.get("author", "migrated"),
        "source": legacy_node.get("source", "legacy-migration"),
        "confidence": legacy_node.get("confidence", 0.8),
        "created_at": legacy_node.get(
            "created_at", datetime.now(timezone.utc).isoformat()
        ),
        "_legacy": True,
    }

    # Limpiar campos vacíos para no ensuciar
    return {k: v for k, v in new_node.items() if v not in (None, "", [], {})}


def convert_edge(legacy_edge: dict) -> dict:
    """Convierte un edge legacy al formato actual."""
    return {
        "from": legacy_edge.get("from", ""),
        "to": legacy_edge.get("to", ""),
        "type": legacy_edge.get("type", "RELATED"),
        "label": legacy_edge.get("label", legacy_edge.get("type", "RELATED")),
    }


def migrate(project_dir: str, dry_run: bool = False) -> dict:
    """Ejecuta la migración. Retorna stats."""
    stats = {"legacy_nodes": 0, "imported": 0, "skipped": 0, "edges": 0}

    # 1. Cargar legacy
    legacy_path = find_legacy_file(project_dir)
    if not legacy_path:
        print("  ❌ No se encontró archivo legacy ckg.json")
        return stats

    print(f"  📂 Legacy: {legacy_path}")
    legacy = load_legacy(legacy_path)

    # 2. Cargar actual
    current_path = find_current_file(project_dir)
    print(f"  📂 Actual: {current_path}")
    current = load_current(current_path)

    existing_ids = {n.get("id", "") for n in current.get("nodes", [])}
    print(f"  📊 Nodos actuales: {len(current.get('nodes', []))}")
    print(f"  📊 Nodos legacy:  {len(legacy.get('nodes', {}))}")

    # 3. Convertir nodos
    legacy_nodes = legacy.get("nodes", {})
    if isinstance(legacy_nodes, dict):
        converted = []
        for nid, node in legacy_nodes.items():
            if nid in existing_ids:
                stats["skipped"] += 1
                continue
            converted.append(convert_node(nid, node))
            stats["imported"] += 1
        stats["legacy_nodes"] = len(legacy_nodes)
    elif isinstance(legacy_nodes, list):
        # Si es lista en vez de dict
        converted = []
        for i, node in enumerate(legacy_nodes):
            nid = node.get("id", f"legacy-{i:03d}")
            if nid in existing_ids:
                stats["skipped"] += 1
                continue
            converted.append(convert_node(nid, node))
            stats["imported"] += 1
        stats["legacy_nodes"] = len(legacy_nodes)
    else:
        print(f"  ❌ Formato de nodes inesperado: {type(legacy_nodes).__name__}")
        return stats

    # 4. Convertir edges
    legacy_edges = legacy.get("edges", [])
    converted_edges = [convert_edge(e) for e in legacy_edges]
    stats["edges"] = len(converted_edges)

    if dry_run:
        print()
        print(f"  🏁 DRY RUN — no se escribió nada")
        print(f"  Se importarían: {stats['imported']} nodos + {stats['edges']} edges")
        print(f"  Omitidos (ya existen): {stats['skipped']}")
        return stats

    # 5. Escribir archivo actualizado
    current["nodes"].extend(converted)
    current["edges"] = converted_edges
    current["generated_at"] = datetime.now(timezone.utc).isoformat()
    current["version"] = "3.0"

    save_current(current_path, current)

    print()
    print(f"  ✅ Migración completada")
    print(f"     Nodos legacy:     {stats['legacy_nodes']}")
    print(f"     Importados:       {stats['imported']}")
    print(f"     Omitidos (dup):   {stats['skipped']}")
    print(f"     Edges:            {stats['edges']}")
    print(f"     Total ahora:      {len(current['nodes'])} nodos")

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Migra CKG legacy al formato actual")
    parser.add_argument(
        "--project",
        default="delta-skill",
        help="ID del proyecto (default: delta-skill)",
    )
    parser.add_argument(
        "--base",
        default=os.getcwd(),
        help="Directorio base del proyecto (default: cwd)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo mostrar qué se migraría, sin escribir",
    )
    args = parser.parse_args()

    print(f"╔{'═' * 50}╗")
    print(f"║  Migración CKG Legacy → Formato Actual{' ' * 15}║")
    print(f"╚{'═' * 50}╝")
    print()
    print(f"  Proyecto:   {args.project}")
    print(f"  Base dir:   {args.base}")
    print()

    try:
        project_dir = find_project_dir(args.base, args.project)
        print(f"  📁 Project dir: {project_dir}")
        migrate(project_dir, dry_run=args.dry_run)
    except FileNotFoundError as e:
        print(f"  ❌ {e}")
        return 1
    except Exception as e:
        print(f"  ❌ Error inesperado: {e}")
        import traceback

        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
