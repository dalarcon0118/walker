# ADR-WALKER-085 — Sprint corriente: un solo resolver para todas las superficies

- **Estado:** aceptado (2026-11-24)
- **Fecha:** 2026-11-24
- **Relacionado:** ADR-WALKER-004/036 (delta SSOT), ADR-WALKER-043 (protocolo
  SSOT), ADR-WALKER-077 (sprint activo por sesión), ADR-WALKER-080 (plan de
  vuelo), INV-004/INV-016.

## Contexto

El 2026-11-24, en `client-chat`, el mismo proyecto reportó tres estados
incompatibles en la misma sesión:

| Superficie | Respuesta observada |
| :--- | :--- |
| `status` | `SPR-Se542f712` activo, 1 spec |
| `cockpit` | `━━━ (sin sprint) ━━━`, `Falta: session_context: sin session_id` |
| `workflow_state` | `SPRINT.ACTIVE`, `Goal: ` vacío, `Tasks: 0/0` |

La sesión `ses_dfaa3d0fdffe5ZSw6amlSdp2Ca` sí apuntaba al sprint en el
DeltaGraph y `resolve_project_sprint` lo encontraba (`resolved_by=active`). El
problema no era el dato: era que cada lector aplicaba una política distinta.

Causas concretas:

1. `SprintService.get_spring_id(chain_dir)` devolvía
   `basename(chain_dir)` = `"client-chat"`. Ese string no es un sprint.
   `prompt_context.build_prompt_extra` y `load_tasks` lo usaban como id: no
   encontraban el sprint real y el motor veía `0/0`.
2. `workflow_state` no aceptaba `session_id`; el prompt nunca podía resolver el
   sprint de la sesión y caía al basename.
3. `status`, `brief`/`cockpit`, `session_context` y `resolve_project_sprint`
   implementaban por separado el orden sesión/activo/planning. Sin `session_id`,
   `brief` no tenía fallback y pintaba "(sin sprint)".
4. `Spring.session_id` vacío en un sprint activo (atribución incompleta,
   ADR-WALKER-077 INV-3) solo generaba un warning de log, invisible para el
   agente.

## Decisión

1. **Un único resolver de lectura**:
   `fast_mcp/features/delta_tracking/application/services/sprint_context.py`
   (`resolve_sprint_context`). Orden fijo:
   **explícito → sesión → activo → planning**.
2. **El `chain_dir` no identifica al sprint.** `get_spring_id` deja de derivar
   el id del basename; delega en el resolver y devuelve `""` si no hay sprint.
   `load_tasks` recibe `session_id` y usa el mismo resolver.
3. **Todas las superficies consumen el mismo `SprintContext`**:
   `status`, `brief`, `cockpit`, `session_context`, `resolve_project_sprint`,
   `design.spring_context`, el prompt del workflow y `workflow_state`.
   `status` descarta el layout legacy `data/spring/**` cuando el DeltaGraph
   responde: el SSOT es delta (INV-004), no un archivo de una fase anterior.
4. **`workflow_state` acepta `session_id`** y publica un bloque `sprint`
   (`spring_id`, `goal`, `status`, `resolved_by`, `session_linked`,
   `spec_count`, `diagnostics`) resuelto por el mismo helper. El prompt y el
   bloque no pueden volver a discrepar.
5. **La degradación se publica, no se esconde**: `diagnostics` incluye
   `session_not_linked`, `sprint_unattributed`,
   `session_points_to_missing_spring:<id>`, `spring_unreadable:<id>`.
   `status` agrega un warning visible cuando hay `sprint_unattributed`.
6. **La atribución sigue siendo la regla de ADR-077**: la sesión es el SSOT de
   pertenencia; el activo del proyecto es solo fallback cuando no hay sesión (o
   la sesión no declara sprint). La atribución válida es `spring.session_id` O
   `spring.context.session_ids`: el campo primario vacío con sesiones en el
   contexto no es un sprint huérfano (falso positivo medido en `client-chat`).
   Un sprint activo sin ninguna sesión dueña no se presenta como sano.

## Invariantes

- **INV-085-1:** dos superficies que consultan el mismo `(chain_dir,
  session_id)` y el mismo estado del DeltaGraph devuelven el mismo
  `spring_id`; si no, hay un bug de resolución, no una diferencia de vista.
- **INV-085-2:** ningún camino de lectura deriva un `spring_id` del basename
  del proyecto.
- **INV-085-3:** `workflow_state` y el prompt del nodo usan el mismo
  `SprintContext`; el prompt no puede tener goal/tareas vacíos mientras el
  bloque `sprint` tiene datos.
- **INV-085-4:** una resolución degradada es visible en `diagnostics`/
  `warnings`; no hay fallback silencioso a "sin sprint".

## failure_scenarios

```json
"failure_scenarios": [
  {
    "fallo": "Un sprint activo queda sin session_id y sin context.session_ids",
    "causa_raiz": "sprint_planning/creación manual activa sin sesión; ADR-077 INV-3 no se validaba",
    "mitigacion": "el resolver emite sprint_unattributed; status lo publica en warnings; session_bind/join atribuye al unirse",
    "deteccion": "diagnostics contiene sprint_unattributed en brief/status/workflow_state"
  },
  {
    "fallo": "La sesión apunta a un SPR que ya no existe (borrado o id corrupto)",
    "causa_raiz": "session.spring_id se escribe una vez y no se reconcilia con el DeltaGraph",
    "mitigacion": "el resolver reporta session_points_to_missing_spring:<id> y cae al activo; no inventa goal",
    "deteccion": "diagnostics del sprint en qualsiasi superficie"
  },
  {
    "fallo": "workflow_state vuelve a mostrar Goal vacío / 0-0 con un sprint real",
    "causa_raiz": "algún caller reintroduce la derivación por basename o no propaga session_id",
    "mitigacion": "test_sprint_context_ssot_bdd compara prompt y resolver; get_spring_id ya no usa basename",
    "deteccion": "tests de prompt/load_tasks y comparación status vs workflow_state en CI"
  },
  {
    "fallo": "status y cockpit vuelven a discrepar cuando la llamada no trae session_id",
    "causa_raiz": "una superficie mantiene su propio fallback o decide no usar el activo",
    "mitigacion": "brief/cockpit/status consumen sprint_context_for; sin sesión usan activo y marcan session_linked=false",
    "deteccion": "test de brief gates contra el resolver y revisión de que no existan cadenas locales"
  }
]
```

## Métricas de calidad por fase

### Fase 1 — Resolver canónico

- [ ] M-02 Una sola política (sesión > activo > planning) y `resolved_by`
      observado en todos los consumidores — anti-patrón a evitar: **Duplicate
      Code / Shotgun Surgery**.
- [ ] M-03 `SprintContext` explícito (goal/status/specs/diagnostics) sin
      entidades anémicas nuevas — anti-patrón a evitar: **Anemic Domain Model**.
- [ ] M-06 Consumidores piden el contrato al composition root
      (`sprint_context_for`); delta no se importa desde workflow/memories —
      anti-patrón a evitar: **Not Invented Here / dependencia invertida**.

### Fase 2 — Propagación de `session_id`

- [ ] M-02 `workflow_state` acepta y propaga `session_id` al prompt y al bloque
      `sprint` — anti-patrón a evitar: **Magic String** (campo ausente del
      schema).
- [ ] M-05 El prompt resuelve goal/tareas con el `SprintContext` de la sesión
      y no con el basename — anti-patrón a evitar: **Hidden Dependency**
      (política implícita en un helper).
- [ ] M-04 La resolución deja rastro en `sprint_resolved_by` y `diagnostics` —
      anti-patrón a evitar: **Eventual Consistency sin safeguards**.

### Fase 3 — Atribución y diagnóstico

- [ ] M-03 `sprint_unattributed` y `session_not_linked` son datos del contrato,
      no logs — anti-patrón a evitar: **Boolean Trap** (estado sin motivo).
- [ ] M-05 `status` publica warnings accionables cuando falta atribución —
      anti-patrón a evitar: **Silent Degradation**.
- [ ] M-04 `resolve_project_sprint` devuelve `diagnostics` y `resolved_by`
      aunque no encuentre sprint — anti-patrón a evitar: **God Class** (un
      handler que decide y oculta).

### Fase 4 — Verificación

- [ ] M-01 Los tests usan `tmp_path` y un DeltaGraph real para
      `get_spring_id`/`load_tasks` — anti-patrón a evitar: **Shared Mutable
      State**.
- [ ] M-02 Un test compara el prompt del workflow y el bloque `sprint` para el
      mismo `(chain, session)` — anti-patrón a evitar: **Test de humo sin
      contrato**.
- [ ] M-06 `client-chat` real verificado por CLI antes de cerrar (status,
      cockpit, workflow_state) — anti-patrón a evitar: **Works on My Machine**.

## Checklist de segundo orden

1. **Módulos que importan esto:** `composition`, `prompt_context`,
   `knowledge` (brief/session_context), `use_cases.status`, `design`,
   `sprint_service`, `handlers/sprint`.
2. **Side effects:** ninguno. El resolver es de lectura; no crea, no cura y no
   escribe el DeltaGraph.
3. **Eventos de dominio:** no publica eventos nuevos. `status` agrega warnings
   al resultado, no al Trace.
4. **Tests que dependen:** `test_brief_bdd`, `test_prompt_context_bdd`,
   `test_workflow_entry_planning_bdd`, `test_fast_mcp_delta_*`,
   `test_sprint_context_ssot_bdd` (nuevo).
5. **Cambia en MCP/CLI/CKG/Delta/Trace:** `workflow_state` acepta
   `session_id` y devuelve `sprint`; `get_spring_id`/`load_tasks` cambian de
   semántica (sprint real, no basename); Delta no cambia de esquema; CKG/Trace
   no cambian.

## Alternativas rechazadas

- **Reparar solo los datos de `client-chat`.** Oculta la causa: cualquier
  proyecto con la misma combinación vuelve a divergir.
- **Que `status` sea el único lector y las demás tools lo llamen.** Acopla
  todas las capas a una tool de presentación y no resuelve `load_tasks`.
- **Derivar el sprint de `chain_dir` con una convención de nombres.** Ya
  falló: el directorio es el proyecto, no el sprint (ADR-WALKER-077).
- **Fallback silencioso a "sin sprint".** Viola INV-016 y produce la mentira
  que este ADR corrige.

## Consecuencias

- `get_spring_id(chain_dir)` puede devolver `""` donde antes devolvía el
  basename. Los llamadores que lo usaban como "nombre del proyecto" deben usar
  `project_id`; los que buscaban un sprint ahora aciertan.
- `load_tasks` requiere el sprint vigente; los tests con repos en memoria
  siguen pasando porque el resolver cae a planning/activo.
- El prompt del workflow y `workflow_state` publican goal y conteos reales; un
  sprint sin atribución se ve como tal en `diagnostics`/`warnings`.
