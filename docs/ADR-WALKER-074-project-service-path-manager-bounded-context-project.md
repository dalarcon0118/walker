# ADR-WALKER-074: ProjectService → bounded context Project; PathManager = rutas del walker (separación de responsabilidades)

> **Status:** Aprobado
> **Fecha:** 2026-09-04
> **Autor:** Kilo (agente) + PO
> **Complementa:** ADR-WALKER-047 (bounded contexts = módulos puros), ADR-WALKER-053 (ProjectContext centralizado), ADR-WALKER-054 (data_dir canónico), ADR-WALKER-071 (port app/path_resolver a fast_mcp)

---

## 0. Resumen ejecutivo (dominio del problema)

Existen dos "managers" que el código confunde:
- **PathManager**: rutas INTERNAS del walker (su propio layout, config, registro PATH, workflow GLOBAL del motor).
- **ProjectService / RegistryStore / use_cases**: todo lo PER-PROYECTO (registro, layout, repo_path, memorias), que vive DENTRO del **bounded context Project**.

El trabajo previo (commit `3286995`) añadió `ProjectService.get_repo_path`/`resolve_from_project_id`/`compose_project_layout`. Este ADR cierra el refinamiento: **ProjectService queda como la fachada de resolución del bounded context Project y DELEGA en sus use cases / RegistryStore (única capa de I/O del registro)**; PathManager queda SOLO con las rutas del walker. Se elimina la doble lectura de `data/projects.json` (ProjectService abría el archivo a mano; RegistryStore también).

---

## 1. Problema

### 1.1 Dos lecturas del mismo SSOT (`data/projects.json`)

`ProjectService.get_repo_path` y `RegistryStore.read/resolve` leen el MISMO `projects.json` con el MISMO formato, en dos implementaciones separadas:

| Fuente de lectura | Dónde | Problema |
|---|---|---|
| `ProjectService.get_repo_path` | `shared_kernel/project_support/project_service.py` | Abre `projects.json` a mano (`json.load`) |
| `RegistryStore.resolve/read/register` | `shared_kernel/registry_store.py` | Abre `projects.json` (root inyectable) |

Dos SSOT de lectura del registro = riesgo de desincronización y de parches divergentes (viola AGENTS.md: un concepto con una sola fuente de verdad).

### 1.2 Responsabilidad del registro mezclada fuera del bounded context Project

El bounded context `Project` (`features/project/` + `shared_kernel/registry_store.py`) es quien OWNS el registro y su layout. Al leer `projects.json` a mano dentro de `ProjectService` (kernel), la responsabilidad de I/O del registro se dispersa fuera del contexto al que pertenece.

### 1.3 Sin una frontera clara: la confusión PathManager ↔ ProjectManager

La pregunta estructural (PO): "¿hace falta crear un ProjectManager nuevo?" La respuesta es NO como clase nueva; **el problema es de delegación, no de falta de tipo**. `ProjectService` YA es la fachada por-responsabilidad del proyecto. Lo que faltaba era que DELEGUE en la capa de I/O del bounded context (RegistryStore) y en los use cases, y que PathManager se limite a las rutas del walker.

---

## 2. Decisión

**`ProjectService` es la fachada pública de resolución del bounded context Project. PathManager es la fachada de rutas del walker. Ninguno lee el registro a mano: el registro lo lee `RegistryStore` (I/O) y los use cases (`features/project/application/use_cases.py`) orquestan comportamientos.**

### D1 — `ProjectService.get_repo_path(project_id, root=None)` delega en `RegistryStore`
- La búsqueda del entry del registro DELEGA en `RegistryStore(root).resolve(project_id)` — único lector de `projects.json`.
- `ProjectService` conserva el fail-fast INV-016: ValueError accionable si no hay registro / no registrado / repo inexistente.
- Se elimina el `json.load` a mano en `ProjectService` (era la V4 del mapeo de responsabilidades).

### D2 — Frontera de responsabilidad explícita (contrato por role)
| Role | Responsabilidad | NO hace |
|---|---|---|
| **PathManager** (+ `paths.py`) | rutas internas del walker: `walker_root`, `registry_path` (la UBICACIÓN del registro), config del walker, workflow GLOBAL del motor | NO resuelve proyectos ni lee el registro de proyectos |
| **RegistryStore** | I/O de `projects.json` (read/register/resolve/resolve_from_cwd) | NO tiene lógica de negocio de proyecto |
| **ProjectService** | fachada de resolución per-proyecto por `project_id` (`get_repo_path`, `resolve_from_project_id`, `resolve_paths`, `compose_project_layout`) | NO lee el registro a mano; delega en RegistryStore |
| **use_cases** (`features/project`) | usos de negocio preparar/listar/estado | NO reimplementa lecturas del registro; usa RegistryStore |

### D3 — Dirección de dependencia (domain-first, AGENTS.md)
- `ProjectService` NO importa las capas de transporte (MCP/CLI) ni `features/` hacia abajo; pero BOTH `ProjectService` y `use_cases` residen en la misma frontera cohesionada del bounded context Project (kernel de proyectos) y comparten `RegistryStore`.
- `core/engine/` y `features/*` siguen resolviendo rutas vía `ProjectService.resolve_paths` / `resolve_from_project_id` — sin cambios de firma para los 20+ callers (backward-compat ya validado).

### D4 — Métricas de calidad por fase (AGENTS.md, mínimo 3 por fase)

**Fase A (I/O del registro):**
- [x] M-01 `get_repo_path` extrae `repo_path` vía una única llamada a `RegistryStore.resolve` — anti-patrón a evitar: reabrir `projects.json` con `json.load` en `project_service.py`
- [x] M-02 El fail-fast INV-016 conserva los 3 errores accionables (sin registro / no registrado / repo inexistente) — anti-patrón: errores vagos o degradación silenciosa a ruta adivinada
- [x] M-03 Los tests de `get_repo_path` pasan sin depender del registro real del repo (root inyectado) — anti-patrón: acoplar tests al projects.json del walker real

**Fase B (frontera PathManager↔ProjectService):**
- [x] M-04 `PathManager` no contiene ninguna referencia a `projects.json` ni `project_id` en su superficie — anti-patrón: PathManager leyendo el registro de proyectos
- [x] M-05 `ProjectService` no abre `projects.json` a mano (grep cero `json.load(registry)`) — anti-patrón: duplicar la I/O del registro fuera de RegistryStore
- [x] M-06 `resolve_paths(chain_dir)` y `resolve_from_project_id(project_id)` comparten `_build_paths` (sin duplicación de tail) — anti-patrón: dos builders divergentes

---

## 3. failure_scenarios

| # | Fallo | Causa raíz | Mitigación | Detección |
|---|---|---|---|---|
| 1 | `get_repo_path` devuelve una ruta distinta de la que el resto del sistema (prepare/status/watcher) resuelve | Dos lecturas de `projects.json` (ProjectService + RegistryStore) con formatos/precedencia divergentes | D1: única lectura vía `RegistryStore.resolve`; test de paridad entre `get_repo_path` y `RegistryStore.resolve` | Test de contrato: `get_repo_path(id) == RegistryStore(root).resolve(id).repo_path` |
| 2 | Regresión: alguien vuelve a abrir `projects.json` a mano en `ProjectService` para evitar el cambio | Plantilla histórica del `json.load` sigue presente | Grep-CI: `json.load` sobre rutas de registro fuera de `registry_store.py`; ADR como referencia de review | CI grep rojo |
| 3 | Migrar `resolve_from_project_id` a un project_id rompe los 20+ callers que pasan `chain_dir` | Cambio de firma o de semántica de `resolve_paths` | `resolve_paths(chain_dir)` mantiene backward-compat (vía legacy por path); `resolve_from_project_id` es aditivo | Suite completa (429+ tests) verde |
| 4 | `PathManager` vuelve a absorber responsabilidad de proyectos (leo el registro ahí) | Frontera no documentada; PR que "optimiza" moviendo la lectura al manager de rutas | D2/D3 documentados + test de completitud de frontera (grep `project_id`/`projects.json` en `path_manager.py`) | CI grep + test de contorno |
| 5 | Error accionable perdido: `get_repo_path` devuelve `""` o `None` en vez de fallar | Eliminar el fail-fast para "no romper" a un caller | INV-016 conservado: ValueError accionable en los 3 casos; test paramétrico | Test: caller recibe ValueError con mensaje recomendando `walker prepare` |

---

## 4. Consecuencias

**Positivas**
- Un solo lector de `projects.json` (RegistryStore) — desaparece la desincronización potencial (V4 del mapeo).
- Frontera conceptual clara y documentada: PathManager = walker; ProjectService/RegistryStore/use_cases = bounded context Project.
- Sin clase nueva innecesaria: `ProjectService` ya es la fachada; se corrige la delegación, no se añade un tipo más.
- Zero ruptura de callers (backward-compat validado por suite completa).

**Negativas / costos aceptados**
- `get_repo_path` pierde el manejo directo de formato del archivo (ahora controlado por RegistryStore) — aceptable porque RegistryStore ES el SSOT de ese formato.
- Frontera de un solo reader: si el registro cambia de formato, se cambia en 1 lugar (RegistryStore), no en 2.

---

## 5. Implementación

1. `ProjectService.get_repo_path` → delega en `RegistryStore(root).resolve(project_id)` (D1) — **HECHO en este ADR**.
2. Verificar suite completa verde (**430 tests**) y test de contrato de paridad `get_repo_path` ↔ `RegistryStore.resolve` — **HECHO**.
3. Actualizar memoria del workstream y proyectar en project.md la sección de frontera de responsabilidades (PathManager vs bounded context Project) — **HECHO** (commit `fe6e339`).

---

## 6. Relaciones y referencias

- ADR-WALKER-047 — bounded contexts = módulos puros; features exportan servicios/use cases.
- ADR-WALKER-053 — ProjectContext centralizado, resolución en el borde.
- ADR-WALKER-054 — data_dir canónico del layout cognitivo.
- ADR-WALKER-071 — port de app/path_resolver a fast_mcp (nacen ProjectService/paths).
- AGENTS.md — DMetrics por fase (M-01..M-06), failure_scenarios obligatorios, checklist de segundo orden.
