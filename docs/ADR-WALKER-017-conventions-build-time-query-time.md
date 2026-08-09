# ADR-WALKER-017: Convenciones derivadas en build-time — ast-grep solo indexa, las consultas leen el CPG

> **Status:** Proposed — pendiente de implementación
> **Fecha:** 2026-08-06
> **Contexto:** CheckConventionsUseCase re-escanea el repo con ast-grep en cada consulta
> **Complementa:** ADR-WALKER-015 (features/project DDD), ADR-WALKER-016 (Bounded Context Protocol)

---

## 1. Contexto y problema

`CheckConventionsUseCase` (features/code_analysis/application/conventions.py) valida las
convenciones del CKG contra el código ejecutando **ast-grep en query time**:

```python
# conventions.py:137-143 — CORRIDO EN CADA CONSULTA
proc = subprocess.run(
    ["ast-grep", "scan", "--rule", tmp_path, repo_root, "--json"],  # ← re-escaneo completo
    ...
)
```

### El anti-patrón

| Hecho | Evidencia |
|---|---|
| **ast-grep re-escanea TODO el repo en cada chequeo** | `ast-grep scan --rule <tmp> <repo_root>` corre sobre todos los archivos, por cada regla, por cada invocación |
| **El resultado no se persiste** | El reporte se calcula en memoria y se descarta; el siguiente chequeo vuelve a escanear |
| **El CPG ya sufrió ese costo en build** | `scan_files()` + `scan_cfg()` + `scan_dfg()` ya recorren el repo en `BuildGraphUseCase.build()` |
| **El CPG ya persiste artefactos derivados** | `quality_report.json` y `bug_proposals.json` se derivan en build y se leen sin re-scan |

**El costo es absurdo e innecesario**: `ast-grep` es costoso (subprocess + parse de AST).
Volver a escanear el repo entero por cada consulta de convenciones es **reconstruir el
índice en cada lectura**, exactamente lo que el CPG existe para evitar.

### El principio

> **ast-grep es la herramienta que GENERA y ACTUALIZA el árbol sintáctico/semántico.
> El CPG es la estructura de datos que lo GUARDA y lo convierte en valor.**
> Las consultas deben ir sobre el CPG (rápido, incremental), nunca sobre ast-grep (caro).

---

## 2. La decisión

**Derivar el reporte de convenciones en build-time** (durante `build()` y `update()`,
donde ast-grep ya corre sobre el código) **y persistirlo** junto al CPG como artefacto.
`CheckConventionsUseCase` pasa a ser **lectura pura** del artefacto: cero scans en query time.

### El patrón objetivo

```
ast-grep ──► SOLO construye/actualiza el CPG (build/update incremental)
                 │
                 ▼
CPG (cpg.json.gz + artefactos derivados persistidos)
   ├─ quality_report.json      ← derivado en build      (ya existe)
   ├─ bug_proposals.json       ← derivado en build      (ya existe)
   └─ conventions_report.json  ← derivado en build      (NUEVO)
                 │
                 ▼
CheckConventionsUseCase = SOLO LECTURA del reporte (0 scans)
```

### Por qué es correcto

| Argumento | Detalle |
|---|---|
| **El costo de ast-grep se paga UNA vez, en build** | En `build()` corre sobre todo el repo; en `update()`/`index_task_files()` corre **solo sobre los archivos cambiados** (el mismo delta que ast-grep ya re-escanea para el outline) |
| **Las consultas son lecturas O(1)** | Leer `conventions_report.json` no parsea código; el índice ya está materializado |
| **Consistente con el resto del CPG** | `quality_report` y `bug_proposals` ya siguen este patrón (derivar en build, leer en query) |
| **El delta incremental ya existe** | `update()` usa `FileWatcher.scan()` → `files_changed` → re-indexa solo eso; el reporte de convenciones debe regenerarse solo para ese delta |

### Qué NO cambia

- **ast-grep sigue siendo el motor de reglas**: las reglas del CKG (`metadata.rule_yaml`)
  se siguen evaluando con ast-grep — pero **en build time**, no en query time.
- **El CKG sigue siendo el SSOT de las reglas**: registrar/versionar una convención
  sigue siendo `walker_kg_store` con `rule_yaml`.
- **`supersedes` sigue funcionando**: el ciclo de vida de convenciones es del CKG,
  no del CPG.

---

## 3. Cambios de diseño

### 3.1 `BuildGraphUseCase._persist_conventions_report()` (nuevo)

Imita `_persist_bug_proposals()`:

```
- En build():  corre las reglas del CKG sobre TODOS los paths (1 vez)
- En update(): corre las reglas SOLO sobre files_changed (delta)
- Persiste:    data/conventions_report.json
```

Estructura del artefacto:

```json
{
  "generated_at": "...",
  "rules_checked": 4,
  "by_rule": {
    "no-silent-except": [
      {"file": "features/x.py", "line": 12, "text": "except: pass", "severity": "error"}
    ]
  },
  "total_violations": 44
}
```

### 3.2 `CheckConventionsUseCase` → lectura pura

```
- Lee data/conventions_report.json
- Si el archivo NO existe (repo nunca indexado): regen con fallback (ast-grep una vez)
  y devuelve el reporte + nota "CPG no estaba indexado — ejecuta scan_code_and_build_cpg"
- Nunca escanea en el camino feliz
```

### 3.3 Convenciones semánticas de grafo

Las reglas que requieren **relaciones del grafo** (no texto) — ej. `unused_functions`
("funciones sin CALLS incoming") — ya se derivan del CPG en `_collect_bug_proposals_core`
(build_service.py:196-283). Se exponen como convenciones reales **sin escanear nada**:
se leen de `bug_proposals.json` y se mezclan en el reporte de convenciones.

---

## 4. Impacto

| Área | Antes | Después |
|---|---|---|
| Consulta de convenciones | ast-grep scan (repo completo) | Lectura de JSON persistido |
| Build | scan outline + cfg + dfg | + reglas de convenciones (mismo repo, 1 pasada) |
| Update incremental | re-index solo cambiados | + reglas solo sobre cambiados |
| Costo query | O(repo) por regla | O(1) |
| Consistency | reporte en memoria, siempre fresco | reporte persistido, fresco si el CPG está fresco |

### Trade-off aceptado

**Staleness**: el reporte de convenciones es un snapshot del último build/update. Si un
archivo cambia sin pasar por `update()` (edición directa), el reporte no lo refleja hasta
el próximo index. Es el MISMO trade-off que ya tienen `quality_report` y `bug_proposals`
— aceptado y consistente con el modelo del walker (el CPG es espejo del código, no un
cálculo en vivo).

---

## 5. Alternativas consideradas

| Alternativa | Por qué se rechaza |
|---|---|
| **Mantener ast-grep en query time** | El costo de re-escaneo completo por consulta es el problema que este ADR resuelve |
| **Indexar los matches en el CPG como nodos** | Hincha el grafo con miles de nodos de violaciones; el reporte JSON es más barato y suficiente (mismo patrón que bug_proposals) |
| **Cache por archivo en query time** | Añade estado mutable y lógica de invalidación en la capa de consulta; el build-time ya tiene el delta exacto |
| **Ejecutar reglas vía CPG (sin ast-grep)** | Las reglas son sintácticas (AST patterns: `except_clause`+`pass_statement`); requieren el árbol de sintaxis. El CPG guarda símbolos y edges, no AST completo — ast-grep es el motor correcto para reglas sintácticas, solo que invocado en build |

---

## 6. Pasos de implementación

1. Agregar `_persist_conventions_report(scan_paths)` a `BuildGraphUseCase`
   (reutiliza `_load_conventions` de conventions.py; lo mueve a un módulo compartido
   o lo importa sin ciclo).
2. Llamarlo en `build()` (paths completos) y en `update()`/`index_task_files()`
   (solo `existing`/`files_changed`).
3. Reescribir `CheckConventionsUseCase.execute()` para leer `conventions_report.json`;
   mantener fallback de regen si no existe.
4. Añadir convención semántica `unused-functions` (lee `bug_proposals.json`).
5. Actualizar tests: `TestCheckConventions` debe verificar lectura del artefacto
   (sin ast-grep) + fallback.

---

## 7. Reglas de diseño (non-negotiables)

- **ast-grep nunca corre en query time** para chequear convenciones.
- **El CKG es el SSOT de las reglas**; el reporte del CPG es una **proyección** materializada.
- **El reporte sigue el patrón de artefactos del CPG**: se deriva en build, se lee en query.
- **Staleness aceptado** (snapshot del último index), consistente con quality/bug reports.
- **Fallback explícito**: si el reporte no existe, regen + nota; nunca `except: pass`.
