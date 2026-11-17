"""shared_kernel/skills — índice de skills del entorno del agente.

El workflow NO conoce los skills que el agente tiene instalados: declarar ids en
el nodo acopla el workflow a la máquina de turno. Lo que declara es **intención**
(`needs` + `tags`), y este índice descubre en runtime qué hay realmente en:

  - `<repo>/.agents/skills/`      (del proyecto, viaja con el repo)
  - `<repo>/.opencode/skills/`    (del proyecto, capa OpenCode)
  - `~/.config/opencode/skills/`  (global del usuario)
  - `<walker>/skills/`            (del propio walker)

El contenido no se inyecta entero: se inyecta el catálogo (nombre +
descripción) y el agente carga el que le sirve con `load_skill`, que es
progressive disclosure. Inlinear cinco skills completos rompe el
`context.budget` del sub-state.

Formato: `<skill>/SKILL.md` (o `SKILL.MD`) con frontmatter YAML plano:

    ---
    name: frontend-design
    description: Produce interfaces distintas...
    tags: [ui, design]
    ---
    cuerpo...
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

SKILL_FILES = ("SKILL.md", "SKILL.MD", "skill.md")
SKILLS_HOME_ENV = "WALKER_SKILLS_HOME"
MAX_DESCRIPTION = 220
MAX_CATALOG_ITEMS = 6
MAX_CATALOG_CHARS = 900
MAX_SKILL_BODY = 8000

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    scope: str = "global"          # project | global | walker
    when_to_use: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    _body: str = field(default="", repr=False)

    def body(self, max_chars: int = MAX_SKILL_BODY) -> str:
        text = self._body.strip()
        if len(text) > max_chars:
            return text[:max_chars] + "\n\n[truncado: el skill es más largo]"
        return text


def _parse_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """YAML plano → dict + cuerpo. Tolera claves anidadas (las ignora)."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    header, body = match.group(1), match.group(2)
    data: dict[str, object] = {}
    lines = header.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.strip() or line.lstrip().startswith("#") or line.startswith((" ", "\t")):
            continue
        key, sep, rest = line.partition(":")
        if not sep:
            continue
        key = key.strip().lower()
        rest = rest.strip()
        # YAML block scalar: `description: >` seguido de líneas indentadas.
        # Sin esto, el catálogo mostraba ">" como descripción (ponytail).
        if rest in (">", "|", ">-", "|-"):
            chunks: list[str] = []
            while index < len(lines) and (lines[index].startswith((" ", "\t")) or not lines[index].strip()):
                chunks.append(lines[index].strip())
                index += 1
            joiner = " " if rest.startswith(">") else "\n"
            data[key] = joiner.join(c for c in chunks if c).strip()
            continue
        if rest.startswith("[") and rest.endswith("]"):
            items = [_parse_scalar(v) for v in rest[1:-1].split(",") if v.strip()]
            data[key] = [i for i in items if i]
        elif rest:
            data[key] = _parse_scalar(rest)
    return data, body


def _skill_file(directory: Path) -> Path | None:
    for name in SKILL_FILES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    # tolera variantes de mayúsculas en el nombre del archivo
    if directory.is_dir():
        for child in directory.iterdir():
            if child.is_file() and child.name.lower() == "skill.md":
                return child
    return None


def read_skill(directory: str | Path, scope: str = "global") -> Skill | None:
    root = Path(directory)
    path = _skill_file(root)
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("skills: no se pudo leer %s (%s)", path, exc)
        return None
    meta, body = _parse_frontmatter(text)
    name = str(meta.get("name") or root.name)
    description = str(meta.get("description") or "").strip()
    if not description:
        for line in body.splitlines():
            if line.strip() and not line.strip().startswith("#"):
                description = line.strip()
                break
    tags = meta.get("tags") or ()
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return Skill(
        name=name,
        description=description[:MAX_DESCRIPTION],
        path=path,
        scope=scope,
        when_to_use=str(meta.get("when_to_use") or meta.get("when-to-use") or ""),
        tags=tuple(str(t) for t in tags if str(t).strip()),
        _body=body,
    )


def skills_home() -> Path:
    """Dónde instala walker los skills globales (los de `hooks install`)."""
    override = os.environ.get(SKILLS_HOME_ENV)
    return Path(override) if override else Path.home() / ".walker" / "skills"


def skills_roots(project_root: str | Path | None, walker_root: str | Path | None = None) -> list[tuple[Path, str]]:
    """Directorios a escanear, en orden de precedencia (primero gana).

    Los skills escritos a mano (proyecto y global del agente) ganan sobre los
    instalados por walker, para que un override local no lo pise el instalador.
    """
    roots: list[tuple[Path, str]] = []
    if project_root:
        base = Path(project_root)
        roots.append((base / ".agents" / "skills", "project"))
        roots.append((base / ".opencode" / "skills", "project"))
        roots.append((base / ".cognitive" / "skills", "project"))
    roots.append((Path.home() / ".config" / "opencode" / "skills", "global"))
    roots.append((skills_home(), "walker"))
    if walker_root:
        roots.append((Path(walker_root) / "skills", "walker"))
    return roots


def discover(roots: list[tuple[Path, str]]) -> dict[str, Skill]:
    """Dedup por nombre con precedencia de `roots` (proyecto primero)."""
    found: dict[str, Skill] = {}
    for root, scope in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            skill = read_skill(child, scope=scope)
            if skill is None:
                continue
            found.setdefault(skill.name, skill)
    return found


# El workflow está en español y la mayoría de los skills en inglés: sin este
# puente, "criterio de diseño" no matchea "frontend design" y el catálogo sale
# vacío siempre (medido con el nodo real).
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "diseno": ("design", "ui", "ux"),
    "interfaz": ("ui", "interface", "frontend"),
    "pantalla": ("ui", "screen", "frontend"),
    "calidad": ("quality", "review"),
    "codigo": ("code", "clean"),
    "arquitectura": ("architecture", "structure", "design"),
    "revision": ("review", "audit"),
    "revisar": ("review",),
    "prueba": ("test", "testing", "qa"),
    "pruebas": ("test", "testing", "qa"),
    "seguridad": ("security", "vulnerability"),
    "documentacion": ("documentation", "docs"),
    "datos": ("data", "database"),
    "base": ("database", "db"),
    "rendimiento": ("performance", "profiling"),
}


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text).lower())
    return "".join(c for c in normalized if not unicodedata.combining(c))


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", _fold(text)) if len(t) > 3]


def score(skill: Skill, needs: str = "", tags: tuple[str, ...] | list[str] = ()) -> int:
    """Match determinista: tags + palabras de la intención (bilingüe)."""
    total = 0
    tag_set = {_fold(str(t)) for t in tags}
    skill_tags = {_fold(t) for t in skill.tags}
    total += 3 * len(tag_set & skill_tags)
    hay_tokens = set(_tokens(f"{skill.name} {skill.description} {skill.when_to_use}"))
    for token in _tokens(needs):
        variants = {token, *_SYNONYMS.get(token, ())}
        if variants & hay_tokens:
            total += 1
    # Un tag también matchea la descripción (con sinónimos): "diseno" debe
    # encontrar un skill que dice "design" aunque no declare tags.
    for tag in tag_set:
        variants = {tag, *_SYNONYMS.get(tag, ())}
        if variants & hay_tokens:
            total += 1
    return total


def search(
    skills: dict[str, Skill],
    needs: str = "",
    tags: tuple[str, ...] | list[str] = (),
    limit: int = MAX_CATALOG_ITEMS,
) -> list[Skill]:
    """Skills que matchean la intención, mejor primero. Sin intención → []."""
    if not needs.strip() and not list(tags):
        return []
    ranked = [(score(s, needs, tags), s) for s in skills.values()]
    hits = [(sc, s) for sc, s in ranked if sc > 0]
    hits.sort(key=lambda pair: (-pair[0], pair[1].name))
    return [s for _, s in hits[:limit]]


def format_catalog(
    skills: list[Skill],
    *,
    limit: int = MAX_CATALOG_ITEMS,
    max_chars: int = MAX_CATALOG_CHARS,
) -> str:
    """Texto listo para el prompt: una línea por skill + cómo cargarla."""
    if not skills:
        return ""
    lines = []
    for skill in skills[:limit]:
        desc = skill.description.replace("\n", " ").strip()
        lines.append(f"- `{skill.name}` — {desc}")
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars].rstrip() + "\n- …"
    return out + (
        "\n\nSi alguna aplica, cargala con `load_skill(<nombre>)` antes de "
        "producir la salida. Si ninguna encaja, resolvé con criterio propio y "
        "decilo explícitamente."
    )
