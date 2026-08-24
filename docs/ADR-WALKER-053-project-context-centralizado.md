# ADR-WALKER-053: ProjectContext centralizado — resolver una vez en el despachador, inyectar a las tasks

> **Status:** Propuesta
> **Fecha:** 2026-08-24
> **Autor:** Kilo (agente) + PO
> **Complementa:** ADR-WALKER-052 §D3/D4 (DI estilo NestJS diferido), ADR-WALKER-046 (AppContainer), SPEC-005 #5 (dispatch único)

---

## 1. Problema

### 1.1 Dos conceptos fusionados en un campo

`ExecutionContext.chain_dir` **no es la raíz del proyecto** — es el directorio del *sprint activo*:

```python
# handlers.py:146 — resolutor central existente
def _ensure_chain_dir(project_id, sprint_id=""):
    ...
    return ProjectService.find_active_sprint(project_id) or ""
```

Las tasks de código lo usan como si fuera la raíz del repo, sobreviviendo solo porque
`ProjectService.resolve_paths(sprint_dir)` sube por el árbol hasta deducir el `project_root`.
Consecuencias observadas (2026-08-24):

| Síntoma | Proyecto | Causa |
|---|---|---|
| `update_incremental_code` / `code_context` / ~15 tasks más devuelven `"chain_dir not available"` vía MCP | react-native-frontend | **No tiene sprint activo** (se cerró formalmente) → `find_active_sprint` = `""` |
| El mismo flujo funcionaba ayer para rnf | react-native-frontend | Tenía sprint activo: el sprint disfrazaba la falta de resolución real |
| `walker <pid>/task` por CLI también falla sin sprint | cualquier proyecto sin sprint | El guard vive en las tasks, no depende del canal |

### 1.2 Resolución duplicada y con precedencia incorrecta

La información explícita (`project_id`/`_project_id` en el payload — inyectada por
`handle_task` L745) llega a la task, pero el guard corta antes de mirarla. Parcheé 3 tasks
individualmente con el mismo bloque (~15 líneas cada una): **el PO señaló correctamente que
eso es copy-paste arquitectónico** — la resolución pertenece al borde (despachador), no a
cada servicio. Es exactamente el patrón NestJS que motivó esta revisión: provider
request-scoped resuelto una vez, inyectado ya resuelto.

### 1.3 Canales de contexto actuales (inventario)

| Canal | Contenido real | Quién lo llena | Consumidores |
|---|---|---|---|
| `ctx.chain_dir` | dir del sprint activo (¡no el repo!) | `handle_task → _ensure_chain_dir` | guards de ~15 tasks + `resolve_paths()` |
| `data["_project_id"]` | id del proyecto del route | `handle_task` L745 | tasks kg/ckg/delta |
| `data["project_id"]` | id explícito del agente | schema de tools (ADR-052) | tasks nuevas (read_code…) |
| `mcp_adapter._resolve_project_id` | precedencia args/env/cwd | capa transporte MCP | solo routing, no construye ctx |

Cuatro canales, dos semánticas distintas (sprint vs repo), cero objeto que los reúna.

---

## 2. Decisión

**Resolver el contexto de proyecto UNA vez, en el despachador, y viajar inyectado — prohibido resolverlo dentro de tasks.**

### D1 — `ProjectContext` como value object único

```python
# features/main/task_execution/application/project_context.py (nuevo)
@dataclass(frozen=True)
class ProjectContext:
    project_id: str          # "" si no resolvió nada
    repo_path: str           # raíz del repo (para tasks de código)
    sprint_chain_dir: str    # dir del sprint activo o "" (para workflow/delta)

    @classmethod
    def resolve(cls, *, payload_pid: str = "", route_pid: str = "") -> "ProjectContext":
        """Precedencia EXPLÍCITO > AMBIENTAL:
        payload.project_id/_project_id > route > env WALKER_PROJECT_ID > cwd."""
```

Única fuente de aritmética de resolución (usa `ProjectService.from_project_id`,
`find_active_sprint`, `resolve_from_registry`). Sin imports de capa transporte.

### D2 — `handle_task` inyecta, no pregunta

En el despachador único (SPEC-005 #5):

```python
pctx = ProjectContext.resolve(
    payload_pid=payload.get("project_id") or payload.get("_project_id") or "",
    route_pid=req.project_id,
)
task_data["_project_ctx"] = pctx.__dict__        # nuevo, para tasks migradas
# ctx.chain_dir CONSERVA su semántica legacy (sprint dir) — cero ruptura:
outcome = execute_public_task(..., chain_dir=pctx.sprint_chain_dir, ...)
```

- `ctx.chain_dir` NO cambia de semántica → las ~15 tasks con guard siguen compilando y,
  donde dependían del sprint, comportándose igual.
- Las tasks de CÓDIGO migran gradualmente a leer `_project_ctx.repo_path`
  (helper `resolve_repo_path(data, ctx)` de una línea disponible en
  `knowledge_helpers`). Migración por lotes, no big-bang.
- Si no hay nada resoluble: UN solo error claro en el despachador
  (`"sin contexto de proyecto — pasá project_id o corré dentro de uno"`)
  en vez de 15 mensajes idénticos dispersos.

### D3 — Prohibición y limpieza

- Nueva regla de revisión: ninguna task nueva escribe resolución de proyecto
  propia (grep CI: `chain_dir not available` solo permitido en handlers + helper).
- Los 3 parches per-task de hoy (`inspect_codebase`, `update_incremental_code`,
  `code_context`) se refactorizan al helper D1 — quedan como consumidores, no
  como implementaciones.

### 2.x Allowlist de excepciones

(vacía al aprobar; toda excepción cita issue y fecha de caducidad)

---

## 3. Escenarios de fallo

| # | Fallo | Causa raíz | Mitigación | Detección |
|---|---|---|---|---|
| 1 | Task de código opera el proyecto equivocado tras cerrarse un sprint | `chain_dir`=sprint-dir usado como raíz; sin sprint → vacío o residuo viejo | `ProjectContext.repo_path` separado de `sprint_chain_dir`; tasks de código consumen repo_path | Test de contrato: `code_context({project_id})` resuelve SIN sprint activo |
| 2 | Precedencia invertida: ambiental (cwd del server) pisa el explícito del agente | Orden de merge en el punto único | Precedencia codificada EN UNA función con test paramétrico (4 fuentes × 2 órdenes) | Suite de precedencia falla |
| 3 | Regresión: alguien copia de nuevo el guard viejo en una task nueva | Plantilla histórica sigue en el código como ejemplo | Grep-CI de la cadena `chain_dir not available` fuera de handlers/helper; ADR como referencia de review | Lint/CI rojo |
| 4 | Sprint activo apunta a proyecto distinto del payload explícito (mixtura silenciosa) | Dos fuentes sin reconciliar | D1 resuelve UNA identidad por despacho; mismatch payload≠route loggea warning | Log de warning + test de mixtura |
| 5 | Proyecto inexistente en registry | Typo del agente en project_id | Error accionable listando proyectos disponibles (`list()`) | Respuesta 400 con next_action |

---

## 4. Consecuencias

**Positivas**
- La familia completa de ~15 guards deja de ser trampa sin tocarlas (compatibilidad total).
- Multi-proyecto nativo real: operar rnf sin sprint activo deja de ser un caso roto.
- Una sola precedencia, testeada una vez (la lección NestJS: el ciclo/contexto imposible-de-mal-resolver > tres meses de bugs).
- Base lista para D4-futuro: si dishka u otro container llega algún día, `ProjectContext` es el provider request-scoped natural.

**Negativas / costos aceptados**
- Un campo legacy más (`_project_ctx` junto a `_project_id`) hasta completar la migración por lotes.
- La semántica dual sprint/repo queda documentada pero convive — deuda visible en este ADR, no oculta en código.
- Riesgo de colisión con la sesión agente paralela en `handlers.py` — coordinar antes del commit.

**Relación**: ejecuta el espíritu DI de ADR-WALKER-052 §D3 sin framework; corrige el supuesto roto que ADR-046 heredó del modelo git-like original.
