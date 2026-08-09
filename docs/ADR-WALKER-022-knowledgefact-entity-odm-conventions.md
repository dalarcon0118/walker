# ADR-WALKER-022: KnowledgeFact es una entidad ODM — convenciones en CKG, CRUD con soft-delete

> **Status:** Accepted
> **Fecha:** 2026-08-06
> **Contexto:** las convenciones del walker (reglas de código, style, decisiones) se modelan como KnowledgeFact en el índice `ckg` del CognitiveGraph. El ciclo de vida y el CRUD no estaban documentados ni verificados contra el código real.
> **Complementa:** ADR-WALKER-016 (Bounded Context Protocol), ADR-WALKER-017 (build-time vs query-time), ADR-WALKER-021 (BDD)
> **Registrado en CKG:** decision-143

---

## 1. Contexto y problema

El conocimiento ingenieril (decisiones, convenciones, patrones, reglas) se persiste en el índice `ckg`
(`data/ckg.json.gz`) vía `CognitiveCkgRepository`. Hay tres formas de clasificar un KnowledgeFact en DDD
y no estaba claro cuál era: **DTO, Value Object o Entidad/Aggregate**.

Además, el flujo real mostraba inconsistencias:
- `save_nodes()` **siempre genera un `_id` nuevo** (`next_id()`) — no hay update in-place.
- `scope` no está en `_FACT_FIELDS` → se normaliza a `metadata` en vez de campo raíz.
- Las convenciones reales del CKG no exponen `rule_yaml` → `conventions.py` las filtra (`if not rule_yaml: continue`).

### El principio rector

> **El CKG es el SSOT del conocimiento; el CPG aporta el código a validar.** Las convenciones son
> entidades con identidad y ciclo de vida, NO documentos planos sin semántica.

---

## 2. La decisión

**KnowledgeFact es una ENTIDAD (ActiveRecord-style vía ODM), no un DTO ni un Value Object.**

| Concepto | ¿Aplica? | Por qué |
|---|---|---|
| **DTO** | ❌ | Los DTOs no tienen identidad ni comportamiento. KnowledgeFact tiene `_id` persistido, estado y ciclo de vida. |
| **Value Object** | ❌ | Los VOs son inmutables y se comparan por valor. Una convención muta su estado (`active` → `superseded`). |
| **Entidad / Aggregate** | ✅ | Tiene **identidad persistida** (`_id: convention-NNN`), **estado mutable** y **comportamiento de agregación** (`supersedes` degrada la reemplazada). |

### El modelo

```python
# features/cognitive_knowledge/entities.py
class KnowledgeFact(Entity):               # Entity = ODM de libs/cognitive_graph/odm
    __index__ = "ckg"
    __search__ = ["statement", "rationale"]
    type: str = fields.String()            # "convention" | "decision" | "pattern" ...
    subject: str = fields.String()
    statement: str = fields.Text(fulltext=True)   # el texto de la regla
    rationale: str = fields.Text(fulltext=True)
    scope: str = fields.String()           # ej. "coding-style"
    tags: list[str] = fields.List()
    metadata: dict = fields.Dict()         # aquí vive rule_yaml (regla ast-grep)
    contains: list[str] = fields.List()
```

`Entity` viene de `libs/cognitive_graph/odm/entity.py` — es un **ODM embebido** (ActiveRecord):
la entidad de dominio ES el documento del índice. No hay DTO de traducción.

### CRUD real (verificado contra el código)

| Operación | Método | Comportamiento real |
|---|---|---|
| **Create** | `save_nodes(nodes)` | `store_many` batch al índice `ckg`. Genera `_id` con `next_id()` (`convention-NNN`). Normaliza campos extra a `metadata`. Aplica `supersedes` (degrada reemplazadas). |
| **Read** | `query(filters)` | `search("ckg", filters)` + **filtra superseded/deprecated** (solo activas). `search(text)` → fulltext sobre statement/rationale. |
| **Update** | NO existe in-place | `save_nodes` genera `_id` nuevo siempre. El "update" real es **crear nueva con `supersedes`** → la vieja queda `status: superseded` (soft-delete). |
| **Delete** | `delete_by_metadata(key, value)` | `search` + `delete_many` batch. **Excepcional**: el ciclo de vida normal es soft-delete vía supersedes, no borrado físico. |

### Datos verificados

- `convention-001` existe en el CKG: la estrategia de manejo de errores del engine
  ("TODO except debe decidir UNA de 5 acciones: propagar, bloquear, reintentar, compensar, loggear").
- Las convenciones `convention-037/038/040/041/043/044` **no exponen `rule_yaml`** en `_source` →
  `conventions.py` las filtra en `_load_conventions` (`if not rule_yaml: continue`).
- `scope` se guarda en `metadata.scope` (no como campo raíz) porque `_FACT_FIELDS` no lo incluye.

---

## 3. El ciclo de vida completo

```
┌──────────────┐     ┌─────────────────────┐     ┌──────────────────────┐
│ 1. CAPTURA    │     │ 2. PERSISTENCIA     │     │ 3. CONSUMO            │
├──────────────┤     ├─────────────────────┤     ├──────────────────────┤
│ kg_extract   │     │ walker_kg_store      │     │ - kg_multi (agent)   │
│ (LLM lee CPG)│──►  │ → SaveToCkgUseCase   │──►  │ - conventions.execute │
│ ckg_type:    │     │ → CognitiveCkgRepo   │     │   (ast-grep)          │
│  convention  │     │ → store_many ckg     │     │ - gates del hook      │
└──────────────┘     └─────────────────────┘     └──────────────────────┘
                          │ 4. VERSIONADO (supersedes)
                          ▼
                status: active ──► status: superseded
                (la nueva degrada la vieja; query() la excluye)
```

1. **Captura**: `kg_extract` pregunta al LLM ("What coding conventions do you observe?",
   `ckg_type: "convention"`, scope `coding-style`) usando el CPG como contexto.
2. **Persistencia**: `walker_kg_store` → `SaveToCkgUseCase` → `CognitiveCkgRepository.save_nodes` →
   `store_many` al índice `ckg`.
3. **Consumo**: el agente consulta vía `kg_multi {ckg:{type:convention}}`; `conventions.execute()`
   las carga y las ejecuta contra el código con ast-grep.
4. **Versionado**: nueva convención con `supersedes` degrada la vieja (`status: superseded`),
   y `query()` la excluye por defecto.

---

## 4. Impacto y trade-offs

| Área | Estado |
|---|---|
| Modelo | KnowledgeFact = entidad ODM (identidad + estado + supersedes) |
| CRUD | C=save_nodes, R=query (filtra superseded), U=crear+supersedes, D=delete_by_metadata (excepcional) |
| Convenciones vs CPG | Viven SOLO en el CKG; el CPG aporta el código a validar |
| Gap detectado | `rule_yaml` no persistido en la mayoría de convenciones → la validación ast-grep opera sobre pocas reglas |

### Trade-offs aceptados

- **Update = soft-delete + crear nueva**: no hay update in-place; cada cambio genera una versión nueva.
  Es consistente con el modelo de conocimiento versionado (trazabilidad).
- **`scope` en metadata**: decisión legacy que convive con el schema del índice (que mapea scope→scope).
  A futuro debería normalizarse a campo raíz.
- **Sin `rule_yaml` en muchas convenciones**: la captura vía LLM no la genera. Pendiente de resolver
  en el ADR-017 (build-time) con un registro explícito de reglas ast-grep.

---

## 5. Alternativas consideradas

| Alternativa | Por qué se rechaza |
|---|---|
| **Modelar convenciones como DTO** | Pierde identidad y ciclo de vida (supersedes) |
| **Modelar como Value Object** | No mutan; las convenciones sí (active → superseded) |
| **Meter convenciones en el CPG** | El CPG guarda estructura de código, no conocimiento; el CKG es el SSOT |

---

## 6. Reglas de diseño (non-negotiables)

- **KnowledgeFact es una entidad ODM con identidad y ciclo de vida.**
- **El CKG es el SSOT de las convenciones; el CPG nunca las almacena.**
- **El ciclo de vida normal es soft-delete** (supersedes → status: superseded); el delete físico es excepcional.
- **`query()` devuelve solo convenciones activas** (excluye superseded/deprecated por defecto).
- **Convención-001**: "TODO except debe decidir UNA de 5 acciones" — aplica a código y a tests.
