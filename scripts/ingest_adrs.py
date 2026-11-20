"""scripts/ingest_adrs.py — ingesta los ADRs de `docs/` al CKG del proyecto.

Problema medido (2026-11-17): el proyecto `walker` estaba registrado y con 42
ADRs en `docs/ADR-*.md`, pero su CKG tenía **0 docs de conocimiento**. Efecto
en cadena: `_seed_knowledge` del harness copiaba 0 nodos al sandbox y la
sección CONOCIMIENTO DEL PROYECTO del prompt se renderizaba vacía; el eval de
workflow pasaba 1/1 sin que el agente viera una sola decisión del proyecto.

Los ids son determinísticos (derivados del filename), así que re-correr la
ingesta actualiza en vez de duplicar.

Uso:
    python3 scripts/ingest_adrs.py                    # dry-run del proyecto walker
    python3 scripts/ingest_adrs.py --apply
    python3 scripts/ingest_adrs.py --project otro --docs-dir docs --apply
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_STATUS_RE = re.compile(r"\*\*\s*(?:status|estado)\s*:?\s*\*\*\s*(.+)", re.IGNORECASE)
_DATE_RE = re.compile(r"\*\*\s*(?:fecha|date)\s*:?\s*\*\*\s*(.+)", re.IGNORECASE)
_ADR_RE = re.compile(r"ADR-WALKER-(\d+)", re.IGNORECASE)


def _clean(value: str) -> str:
    """Quita markdown/emoji de un valor de metadata."""
    value = value.replace("*", "").replace("`", "").strip()
    value = re.sub(r"^[>:\-\s]+", "", value)
    return value.strip(" .")


_LIST_MARKER = re.compile(r"^(?:[-*+]|\d+\.)\s+")
# Líneas de cabecera en negrita (`**Status:** Implemented`) que NO son prosa.
_META_KEY = re.compile(
    r"^\*\*\s*(status|estado|date|fecha|updated|authors?|autor(?:es)?|related|"
    r"complementa|hip[oó]tesis|contexto)\b",
    re.IGNORECASE,
)


def _paragraphs(lines: list[str], start: int) -> list[str]:
    """Párrafos sustantivos del cuerpo (sin metadata, tablas, headings, fences)."""
    paragraphs: list[str] = []
    buf: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            if buf:
                paragraphs.append(" ".join(buf))
                buf = []
            continue
        if stripped.startswith(("```", ">", "|", "---")) or stripped.startswith("#"):
            if buf:
                paragraphs.append(" ".join(buf))
                buf = []
            continue
        if _META_KEY.match(stripped):
            if buf:
                paragraphs.append(" ".join(buf))
                buf = []
            continue
        marker = _LIST_MARKER.match(stripped)
        if marker:
            stripped = stripped[marker.end():]
        buf.append(stripped)
    if buf:
        paragraphs.append(" ".join(buf))
    return paragraphs


def _first_paragraph(lines: list[str], start: int) -> str:
    """Resumen del ADR: el primer párrafo con cuerpo suficiente.

    Se prefiere un párrafo de >= 80 chars porque muchos ADRs arrancan con un
    rótulo en negrita de una línea (`**Facts** (...)`) que no resume nada.
    """
    paragraphs = [p for p in _paragraphs(lines, start) if len(p) >= 40]
    if not paragraphs:
        return ""
    substantial = [p for p in paragraphs if len(p) >= 80]
    return (substantial or paragraphs)[0]


#: Cuánto cuerpo del ADR se indexa. El retrieval necesita la decisión, no la
#: cabecera: indexar sólo el primer párrafo dejaba ~800 chars de Status/Date y
#: el golden set en 0/20 (ADR-067 no aparecía para "por qué el engine no puede
#: importar infraestructura" porque la respuesta vive en el cuerpo).
SEARCH_BODY_LIMIT = 8_000


def _search_body(lines: list[str], start: int, limit: int = SEARCH_BODY_LIMIT) -> str:
    """Párrafos sustantivos del ADR hasta `limit` chars, en orden."""
    chunks: list[str] = []
    size = 0
    for paragraph in _paragraphs(lines, start):
        if len(paragraph) < 40:
            continue
        chunks.append(paragraph)
        size += len(paragraph) + 2
        if size >= limit:
            break
    return "\n\n".join(chunks)[:limit]


_STATUS_VOCAB: dict[str, str] = {
    "accepted": "accepted",
    "aceptado": "accepted",
    "aceptada": "accepted",
    "aprobado": "accepted",
    "aprobada": "accepted",
    "propuesta-aprobada": "accepted",
    "active": "accepted",
    "activa": "accepted",
    "activo": "accepted",
    "proposed": "proposed",
    "propuesta": "proposed",
    "propuesto": "proposed",
    "in-progress": "in-progress",
    "en-progreso": "in-progress",
    "implemented": "implemented",
    "implementado": "implemented",
    "ejecutado": "implemented",
    "completado": "implemented",
    "aplicada": "implemented",
    "applied": "implemented",
    "draft": "draft",
    "superseded": "superseded",
}


def _status_tag(status: str) -> str:
    """`In Progress — F1-F8 + wiring` → `status:in-progress`.

    Se corta en la primera raya/parentesis porque el tag salía con toda la
    frase (`status:in-progress-f-f-wiring-del-dominio-al-runtime`) y dejaba de
    ser vocabulario consultable. Los estados vienen en español y en inglés, así
    que se normalizan a un set canónico de 6 valores.
    """
    if not status:
        return ""
    head = re.split(r"[—–(:]", status, maxsplit=1)[0].strip()
    slug = "-".join(w.lower() for w in re.findall(r"[A-Za-z]+", head)[:3])
    canonical = _STATUS_VOCAB.get(slug) or _STATUS_VOCAB.get(slug.split("-")[0])
    return f"status:{canonical}" if canonical else ""


def parse_adr(path: Path) -> dict:
    """Convierte un ADR markdown en un nodo de CKG (id determinístico)."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    title = ""
    title_idx = 0
    for idx, line in enumerate(lines):
        if line.startswith("# "):
            title = line[2:].strip()
            title_idx = idx
            break
    if not title:
        title = path.stem

    header = "\n".join(lines[: title_idx + 8])
    status_match = _STATUS_RE.search(header)
    date_match = _DATE_RE.search(header)
    status = _clean(status_match.group(1)) if status_match else ""
    date = _clean(date_match.group(1)) if date_match else ""

    match = _ADR_RE.search(title) or _ADR_RE.search(path.name)
    adr_code = f"ADR-WALKER-{match.group(1)}" if match else path.stem.upper()

    summary = _first_paragraph(lines, title_idx + 1)
    if len(summary) < 60:
        summary = _first_paragraph(lines, title_idx + 1 + 6)

    # F2: se indexa el cuerpo sustantivo, no sólo el primer párrafo. El
    # `description` es lo que entra al FTS (campos statement + rationale).
    body = _search_body(lines, title_idx + 1)
    if body.startswith(summary):
        description = body
    elif summary:
        description = f"{summary}\n\n{body}"
    else:
        description = body

    from scripts.derive_knowledge_tags import derive_tags

    tags = derive_tags(title, description[:1500])
    tags.add("architecture")
    tag = _status_tag(status)
    if tag:
        tags.add(tag)

    return {
        "id": path.stem.lower(),
        "label": title,
        "description": description[: SEARCH_BODY_LIMIT + 500],
        "type": "design-decision",
        "tags": sorted(tags),
        "metadata": {
            "source": f"docs/{path.name}",
            "adr": adr_code,
            "status": status,
            "date": date,
        },
    }


def build_nodes(docs_dir: Path) -> list[dict]:
    return [parse_adr(p) for p in sorted(docs_dir.glob("ADR-*.md"))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="walker", help="project_id registrado")
    ap.add_argument("--docs-dir", default="docs", help="dir con los ADR-*.md")
    ap.add_argument("--apply", action="store_true", help="escribe (default: dry-run)")
    args = ap.parse_args()

    from fast_mcp.features.cognitive_knowledge.application.services.ckg_write import (
        CkgWriteService,
    )
    from fast_mcp.shared_kernel.project_support import ProjectService

    registry = json.loads(
        (Path(__file__).resolve().parent.parent / "data/projects.json").read_text()
    )["projects"]
    entry = registry.get(args.project)
    if not entry:
        print(f"proyecto desconocido: {args.project}", file=sys.stderr)
        return 2

    repo = Path(entry["repo_path"])
    docs_dir = repo / args.docs_dir
    nodes = build_nodes(docs_dir)
    print(f"proyecto: {args.project} · {docs_dir} · ADRs: {len(nodes)}")
    for node in nodes[:3]:
        print(f"  {node['id']:<52} {node['type']:<16} tags={node['tags']}")
    if len(nodes) > 3:
        print(f"  ... y {len(nodes) - 3} más")

    if not args.apply:
        print("\n(dry-run) volvé a correr con --apply para escribir.")
        return 0
    if not nodes:
        print("nada que ingerir")
        return 0

    from returns.pipeline import is_successful

    result = CkgWriteService.save_nodes(str(repo), nodes, task_id="ingest-adrs")
    if not is_successful(result):
        print(f"falló: {result.failure()}", file=sys.stderr)
        return 1
    print(f"\naplicado: {result.value_or({})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
