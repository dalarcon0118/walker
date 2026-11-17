"""test_skills_index_bdd.py — índice de skills del entorno.

El workflow declara INTENCIÓN (needs/tags), no ids: qué skills existen depende
de lo que el agente tenga instalado. El índice descubre, deduplica por
precedencia y matchea de forma determinista; el contenido se carga a demanda.
"""

from __future__ import annotations

import json
from pathlib import Path

from fast_mcp.shared_kernel.skills import (
    MAX_CATALOG_ITEMS,
    discover,
    format_catalog,
    read_skill,
    search,
    skills_roots,
)


def _skill(root: Path, name: str, description: str, tags: list[str] | None = None,
           body: str = "Instrucciones del skill.\n", file_name: str = "SKILL.md") -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}", f"description: {description}"]
    if tags:
        lines.append("tags: [" + ", ".join(tags) + "]")
    lines.append("---")
    (directory / file_name).write_text("\n".join(lines) + "\n" + body, encoding="utf-8")
    return directory


def test_read_skill_parsea_frontmatter_y_cuerpo(tmp_path):
    directory = _skill(tmp_path, "frontend-design", "Interfaces distintas", ["ui", "design"])

    skill = read_skill(directory, scope="project")

    assert skill.name == "frontend-design"
    assert skill.description == "Interfaces distintas"
    assert skill.tags == ("ui", "design")
    assert skill.scope == "project"
    assert skill.body().startswith("Instrucciones del skill.")


def test_sin_frontmatter_usa_directorio_y_primera_linea(tmp_path):
    directory = tmp_path / "taste"
    directory.mkdir()
    (directory / "SKILL.md").write_text("# Taste\nCriterio visual para pantallas.\n")

    skill = read_skill(directory)

    assert skill.name == "taste"
    assert "Criterio visual" in skill.description


def test_acepta_skill_md_en_mayusculas(tmp_path):
    directory = _skill(tmp_path, "mayus", "desc", file_name="SKILL.MD")
    assert read_skill(directory).name == "mayus"


def test_discover_deduplica_por_precedencia(tmp_path):
    project = tmp_path / "repo"
    global_dir = tmp_path / "global"
    _skill(project, "ux", "versión del proyecto", ["ux"])
    _skill(global_dir, "ux", "versión global", ["ux"])
    _skill(global_dir, "solo-global", "otra", ["misc"])

    skills = discover([(project, "project"), (global_dir, "global")])

    assert skills["ux"].description == "versión del proyecto"
    assert skills["solo-global"].scope == "global"


def test_search_por_tags_y_por_texto(tmp_path):
    _skill(tmp_path, "frontend-design", "Interfaces distintas y tipografía", ["ui", "design"])
    _skill(tmp_path, "db-review", "Revisión de esquemas y migraciones", ["database"])
    skills = discover([(tmp_path, "project")])

    by_tag = search(skills, "", ("design",))
    by_text = search(skills, "revisar migraciones de base de datos")
    sin_intencion = search(skills, "", ())

    assert [s.name for s in by_tag] == ["frontend-design"]
    assert [s.name for s in by_text] == ["db-review"]
    assert sin_intencion == [], "sin intención no se inyecta catálogo"


def test_search_respeta_el_limite(tmp_path):
    for i in range(8):
        _skill(tmp_path, f"skill-{i}", "diseño de interfaces", ["design"])

    hits = search(discover([(tmp_path, "project")]), "", ("design",))

    assert len(hits) == MAX_CATALOG_ITEMS


def test_format_catalog_incluye_instruccion_de_carga(tmp_path):
    _skill(tmp_path, "ux", "criterio visual", ["design"])

    text = format_catalog(search(discover([(tmp_path, "project")]), "", ("design",)))

    assert "`ux` — criterio visual" in text
    assert "load_skill(<nombre>)" in text
    assert format_catalog([]) == ""


def test_body_se_trunca(tmp_path):
    directory = _skill(tmp_path, "largo", "desc", body="x" * 9000)
    skill = read_skill(directory)

    body = skill.body(max_chars=1000)

    assert len(body) <= 1100
    assert "truncado" in body


def test_skills_roots_prioriza_proyecto(tmp_path):
    roots = skills_roots(tmp_path, tmp_path / "walker")

    assert roots[0] == (tmp_path / ".agents" / "skills", "project")
    assert roots[1] == (tmp_path / ".opencode" / "skills", "project")
    assert roots[-1] == (tmp_path / "walker" / "skills", "walker")


def test_match_bilingue_espanol_ingles(tmp_path):
    """El workflow está en español y los skills en inglés: sin sinónimos, vacío."""
    dir_ = tmp_path / "frontend-design"
    dir_.mkdir()
    (dir_ / "SKILL.md").write_text(
        "---\nname: frontend-design\ndescription: Create distinctive frontend "
        "interfaces with high design quality\n---\ncuerpo\n"
    )
    skills = discover([(tmp_path, "project")])

    assert search(skills, "criterio de diseño para la pantalla")
    assert search(skills, "", ("diseno",)), "tag sin tilde también matchea"
