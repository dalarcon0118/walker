# Workflow Reconciliation — Diseño (Analogía git → walker)

> **Estado:** diseño — en implementación directa para desbloquear el sprint formal `session-20260731`.
> **Fecha:** 2026-08-05
> **Problema que resuelve:** el árbol del workflow puede quedar en estado "roto" (sin mecanismos de reconciliación) cuando el agente pausa el flujo para cambiar de contexto, y ninguna gate puede volver a dispararse.

---

## 1. Contexto del problema

El workflow del walker se comporta como un árbol de flujos (similar a git-flow):

- `SPRINT.PLANNING → PLAN_APPROVAL → TASK_SPEC → TASK_REVIEW → ACTIVE → REVIEW → ...`
- Los nodos se conectan por gates (edges) que validan precondiciones.

**Caso real detectado (session-20260731):**
El workflow estaba **especificando tareas** (TASK_SPEC/TASK_REVIEW) cuando se decidió **detener para agregar más tareas al sprint**. Al intentarlo, el estado quedó con `sub_state_index=5` (apunta a REVIEW) mientras `current_sub_state=TASK_REVIEW` (índice 3). Resultado: **todas las gates del nodo SPRINT quedaron bloqueadas** por la validación de precondiciones (`check_transition_preconditions`, `services.py:60-67`), porque recorre hasta el índice 5 e incluye `REVIEW` sin su `output_schema.decision`.

**Diagnóstico estructural:** el workflow **no tiene mecanismos de reconciliación** — análogo a un git sin `stash`, sin `rebase/merge`, sin `--force` documentado y sin `reflog`.

---

## 2. Estrategias de git para el mismo caso

El caso "estoy en medio de un trabajo (WIP) y necesito cambiar de contexto, hacer otra cosa y luego volver" está **resuelto en git** con 4 estrategias complementarias. La documentación oficial de `git-stash` lo describe como *"Interrupted workflow: when you are in the middle of something, your boss comes in and demands that you fix something immediately"* — el patrón canónico es:

```
$ git switch -c my_wip      # checkpoint del trabajo en curso (rama WIP)
$ git commit -a -m "WIP"    # guardar el estado incompleto
$ git switch master         # cambiar de contexto
$ ... hacer lo urgente ...
$ git switch my_wip         # volver
$ git reset --soft HEAD^    # reanudar
```

| Estrategia git | Qué hace | Por qué no falla |
|---|---|---|
| **Stash / WIP branch** | `git stash` / commit-WIP: guarda el trabajo incompleto, cambia de contexto, vuelve y restaura. | El estado incompleto se preserva como checkpoint; no se pierde ni se corrompe. |
| **Feature branch (isolación)** | El trabajo diverge en una rama; el main queda estable. Luego se reconcilia (merge/rebase). | Dos estados "perfectamente posibles" coexisten: el main y la feature. |
| **Rebase / merge (reconciliar)** | `git merge` (3 vías, conserva ambos) o `git rebase` (linealiza). Resuelve conflictos. | La base se actualiza y la rama vuelve a encajar; el árbol nunca queda sin salida. |
| **Force con documentación** | `git push --force-with-lease` / `git branch -D`: fuerza solo con intención explícita; el **reflog** siempre permite recuperar. | El override queda registrado y es recuperable; nunca es destructivo ciego. |

**Principio estructural de git:** el grafo de commits es **inmutable y recuperable (reflog)** — nunca queda "roto" de forma irreversible. Toda operación destructiva exige intención explícita y deja rastro.

---

## 3. Los 4 gaps del walker (y su fix)

### GAP-1: No hay checkpoint / stash del estado de especificación
El estado persiste `sub_state_index` y `current_sub_state` como dos campos **redundantes sin invariante de coherencia** (`WorkflowState.from_dict`, `models.py:617-648`). Cuando se "pausa especificar → agregar tasks", no hay mecanismo para resumir: el árbol se corrompe (índice 5 vs TASK_REVIEW).

**Fix (reconciliación por invariante):** al cargar el estado (`from_dict`), recomputar/validar `sub_state_index` a partir de `current_sub_state`:
- Si `current_sub_state` tiene un índice correcto conocido → usar ese índice.
- Si hay desfase → alertar (logging) y reconciliar al valor coherente.
- (Auto-reparación de la corrupción existente sin tocar datos a mano.)

### GAP-2: No hay gate "suspender especificación → editar backlog"
`edit_plan` solo existe desde `ACTIVE` (`edges.json:108-107`). Desde `TASK_SPEC`/`TASK_REVIEW` no hay ninguna salida hacia `PLANNING`. En términos git: la feature branch no tiene camino de vuelta al main.

**Fix (gate de suspensión):** agregar una gate disponible desde los sub-estados de especificación (`TASK_SPEC`, `TASK_REVIEW`) que transicione a `SPRINT.PLANNING` — preservando el punto de reanudación (posición + specs previas). Es el *stash* de git.

### GAP-3: No hay gate de "reanudar especificación" con reconciliación
Una vez editado el backlog, no hay mecanismo para volver al punto de especificación validando que las specs previas sigan coherentes.

**Fix (gate de reanudación):** al reanudar, el workflow reconcilia el estado (índice/nombre), valida que las specs completadas sigan siendo coherentes con el nuevo plan y reubica al agente en `TASK_SPEC`/`TASK_REVIEW`. Es el *merge/rebase* al volver.

### GAP-4: No hay force documentado
Las precondiciones pueden bloquear TODAS las gates de un nodo (caso actual) sin escape. No existe override con justificación registrada.

**Fix (force graduado con documentación):** gate/task de transición forzada que salta precondiciones SOLO con `--reason` obligatorio, registrando la decisión en CKG (decisión) + trace (evento `WORKFLOW_FORCED`). Inspirado en `--force-with-lease`: fuerza con intención explícita y rastro recuperable.

---

## 4. Modelo de estados reconciliable (git-flow aplicado)

En vez de tratar "especificar" y "agregar tareas" como estados excluyentes, se modelan como **dos ramas reconciliables**:

```
                    [suspender]               [reanudar]
   TASK_SPEC/TASK_REVIEW ───────────→ SPRINT.PLANNING ───────────→ TASK_SPEC/TASK_REVIEW
        (punto de reanudación)          (editar backlog)          (reconciliar + validar specs)
              ↑                                                          │
              └────────────── (todas las gates de SPRINT) ───────────────┘
                                con force documentado como escape
```

- **Suspender** = stash (guarda posición + specs previas).
- **Reanudar** = merge/rebase (reconcilia índice/nombre, valida coherencia).
- **Force** = `--force-with-lease` (intención explícita + registro en CKG/trace).

---

## 5. Alcance de implementación (checklist)

1. **Reconciliación por invariante en `from_dict`** — `features/engine/domain/models.py`
2. **Gate de suspensión** (`suspend_specification` desde TASK_SPEC/TASK_REVIEW → PLANNING) — `features/engine/workflow/edges.json` + nodo SPRINT
3. **Gate de reanudación** (`resume_specification` desde PLANNING → TASK_SPEC con reconciliación) — `features/engine/workflow/edges.json` + nodo SPRINT
4. **Force documentado** — transición con `--reason` obligatorio + registro en CKG/trace
5. **Verificación** — `walker status` de `session-20260731` sin bloqueo de gates

---

## 6. Registro en sprint

Estas tareas son **prerrequisito para continuar** el sprint formal `session-20260731`. Al completarlas, se registrarán en el plan del sprint con estado **resuelta** (done), sin necesidad de re-especificarlas por el flujo completo.

---

*Documento creado desde el análisis git→walker. Los archivos citados son la SSOT del comportamiento actual.*
