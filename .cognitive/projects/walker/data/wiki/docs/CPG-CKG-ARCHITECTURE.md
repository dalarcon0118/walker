# CPG + CKG — Arquitectura de Grafos para Contexto LLM

> **Estado:** Implementado
> **Versión:** 1.0
> **Dependencias:** `walker.py`, `cpg.py`, `graph_utils.py`, `ckg/`
> **Comandos:** `cpg-grep`, `cpg-read`, `cpg-build`, `ckg-query`, `deltagraph`

---

## 1. Visión General

El proyecto delta-skill tiene **dos grafos complementarios** que el LLM usa para entender el proyecto sin leer archivos crudos:

```
┌─────────────────────────────────────────────────────────┐
│                     DELTA-SKILL                          │
│                                                          │
│  ┌──────────────────┐    ┌──────────────────┐            │
│  │       CPG         │    │       CKG         │           │
│  │  Code Property    │    │  Code Knowledge   │           │
│  │     Graph         │    │     Graph         │           │
│  ├──────────────────┤    ├──────────────────┤            │
│  │ Código fuente     │    │ Conocimiento      │           │
│  │ estructurado      │    │ cognitivo         │           │
│  │                   │    │                   │           │
│  │ files             │    │ decisions         │           │
│  │ functions         │    │ conventions       │           │
│  │ classes           │    │ modules           │           │
│  │ imports           │    │ components        │           │
│  │ calls             │    │ patterns          │           │
│  │                   │    │ warnings          │           │
│  │ bugs (CFG/DFG)    │    │ arquitectura      │           │
│  │ seguridad (taint)  │    │ eventos           │           │
│  └──────────────────┘    └──────────────────┘            │
│         │                        │                        │
│         └──────────┬─────────────┘                        │
│                    │                                       │
│          ┌─────────▼─────────┐                             │
│          │   DeltaGraph      │                             │
│          │  (graph_utils)    │                             │
│          │  CPG + CKG +      │                             │
│          │  Sprint Tasks     │                             │
│          └───────────────────┘                             │
│                                                          │
│  ┌────────────────────────────────────────────────────┐   │
│  │              Walker commands                        │   │
│  │  cpg-grep  |  cpg-read  |  ckg-query  | deltagraph │   │
│  └────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

---

## 2. CPG — Code Property Graph

### 2.1 Qué es

El CPG es un **grafo estructural del código fuente**. Se construye automáticamente a partir de los archivos del proyecto usando `sg outline` (ast-grep) + analizadores CFG/DFG propios.

### 2.2 Fuentes de datos

| Fuente | Tecnología | Qué extrae |
|--------|-----------|------------|
| **Estructural** | `sg outline` | files, functions, classes, imports (top-level) |
| **CFG** (Control Flow) | `_scan_cfg` (AST Python) | dead branches, missing returns, infinite loops, function-level CALLS |
| **DFG** (Data Flow) | `_scan_dfg` (AST Python) | uninitialized vars, taint paths, data gaps, hardcoded secrets |

### 2.3 Nodos y Edges

```
Tipos de nodo:
  file, function, method, class, module, constant, variable

Tipos de edge:
  CONTAINS  → file contiene función/clase
  CALLS     → función llama a otra función (module-level y function-level)
  IMPORTS   → archivo importa módulo
```

### 2.4 Queries disponibles

```
── Estructura ──
  function:set_task_status     → busca función por nombre
  class:SprintState            → busca clase
  file:state.py type:function  → funciones en un archivo
  file:spring/*                → todos los nodos en spring/

── Relaciones ──
  contains:set_task_status     → qué archivo contiene este símbolo
  calls:complete_task          → qué funciones llaman a complete_task
  imports:json file:spring/*   → archivos en spring/ que importan json

── Bugs (CFG) ──
  bug:dead_branches            → código sin camino a return
  bug:missing_returns          → funciones sin return en todas las rutas
  bug:infinite_loops           → loops sin break ni exit

── Bugs (DFG) ──
  bug:uninitialized_vars       → variables leídas sin asignar
  bug:taint_paths              → datos de fuente no confiable a sink

── Seguridad ──
  security:all                 → todos los patrones
  security:command_injection   → subprocess.run, os.system
  security:data_leak           → print/logging con datos de input
  security:weak_crypto         → md5, sha1, random no criptográfico
  security:path_traversal      → os.remove, shutil.rmtree

── Traza ──
  trace:complete_task->set_task_status  → camino entre funciones
```

### 2.5 Ejemplos del proyecto

```bash
# Encontrar el bug: funciones que leen tasks en vez de progress.task_states
cpg-grep "contains:set_task_status"
# → state.py contiene set_task_status

# Ver qué funciones llaman a complete_task
cpg-grep "calls:complete_task"
# → cmd_transition → complete_task (CALLS)

# Detectar código muerto en el cierre del sprint
cpg-grep "bug:dead_branches file:spring/close.py"
# → Branch sin camino a return (L194, L155, L298)

# Detectar fugas de datos
cpg-grep "security:data_leak file:state.py"
# → open → print (L111), open → sprint_state.get (L170)
```

---

## 3. CKG — Code Knowledge Graph

### 3.1 Qué es

El CKG es un **grafo de conocimiento cognitivo** sobre el proyecto. No contiene código fuente — contiene **decisiones, reglas, convenciones, advertencias, componentes**, y cómo se relacionan entre sí y con las tareas del sprint.

### 3.2 Nodos disponibles (en el proyecto)

| Tipo | Cantidad | Significado |
|------|----------|-------------|
| `convention` | 71 | Reglas y buenas prácticas ("OrchestrationService como entry point") |
| `decision` | 38 | Decisiones arquitectónicas ("ADR: CKG reemplaza facts.json") |
| `module` | 10 | Módulos del proyecto ("workflow_engine", "spring/") |
| `component` | 6 | Componentes de software ("TaskRegistry", "SideEffectEngine") |
| `outside_scope` | 9 | Lo que está fuera del alcance actual |
| `layer` | 4 | Capas arquitectónicas ("domain", "application", "infrastructure") |
| `pattern` | 5 | Patrones de diseño ("Repository", "Service Layer") |
| `api` | 2 | APIs expuestas ("WorkflowAPI", "HealthAPI") |
| `event` | 8 | Eventos de dominio |
| `quality` | 1 | Métricas de calidad |
| `class`, `function` | 9 | Clases y funciones documentadas |

### 3.3 Relaciones disponibles

| Edge | Significado |
|------|-------------|
| `BELONGS_TO` | Pertenencia a módulo/capa |
| `IMPLEMENTS` | Implementación de interfaz/patrón |
| `DEPENDS_ON` | Dependencia entre componentes |
| `FOLLOWS` | Sigue una convención |
| `DECIDED_IN` | Decisión documentada en tarea |
| `SUPERSEDES` | Reemplaza a otro nodo |
| `RELATED_TO` | Relación genérica |

### 3.4 Queries disponibles

```
── Por tipo ──
  type:decision              → todas las decisiones
  type:convention            → todas las convenciones
  type:module                → todos los módulos

── Por contexto ──
  scope:workflow_engine      → conocimiento sobre un módulo
  scope:TASK-013             → conocimiento sobre una tarea

── Por texto ──
  OrchestrationService       → búsqueda libre en label/description
  "único punto de entrada"   → frases exactas

── Navegación ──
  neighbors:OrchestrationService  → vecinos en el grafo

── Combinaciones ──
  type:decision scope:workflow    → decisiones sobre workflow
  type:convention scope:engine    → convenciones sobre el engine
```

### 3.5 Ejemplos del proyecto

```bash
# ¿Qué decisiones afectan al workflow?
ckg-query "type:decision scope:workflow"
# → ADR: CKG reemplaza facts.json
# → ADR: Nuevo nodo DESIGN en el workflow

# ¿Qué reglas rigen el OrchestrationService?
ckg-query "type:convention OrchestrationService"
# → OrchestrationService como unico punto de entrada para el LLM
# → WorkflowAPI como capa delgada sobre OrchestrationService

# ¿Qué está fuera de alcance?
ckg-query "type:outside_scope"
# → La autenticación no es responsabilidad del engine
# → El despliegue no es responsabilidad del skill

# ¿Qué componentes se relacionan con TaskRegistry?
ckg-query "neighbors:TaskRegistry"
# → SprintStateRepository (DEPENDS_ON)
# → WorkflowGraph (DEPENDS_ON)
# → TaskRegistry (BELONGS_TO: domain)
```

---

## 4. DeltaGraph — Contexto Federado

### 4.1 Qué es

DeltaGraph unifica CPG + CKG + datos del sprint en un solo grafo navegable. Permite al LLM obtener **contexto completo de una tarea** en una sola consulta.

### 4.2 Flujo de datos

```
                  DeltaGraph (graph_utils.py)
                         │
          ┌──────────────┼──────────────┐
          │              │              │
       plan.json    cpg.json      ckg.json
       (tasks,      (código      (conocimiento
      predicates,   estructurado) cognitivo)
      risks)
          │              │              │
          └──────────────┼──────────────┘
                         │
            unified_context(task_id)
                         │
               contexto para el LLM
```

### 4.3 Queries disponibles

```
── Contexto de tarea ──
  deltagraph TASK-013
  → task info + code context + CKG knowledge + risks + dependencies

── Resumen del sprint ──
  deltagraph
  → sprint overview + CPG stats + CKG stats
```

---

## 5. Cómo debe usar el LLM estos módulos

### 5.1 Orden de consulta recomendado

```
1. ckg-query "type:convention scope:<módulo>"
   → Aprender las reglas del módulo antes de tocarlo

2. ckg-query "type:decision scope:<módulo>"
   → Entender por qué se hicieron ciertas elecciones

3. cpg-read <archivo_principal>
   → Ver estructura del código (funciones, firmas, imports)

4. cpg-grep "contains:función_principal"
   → Encontrar dónde está lo que necesito modificar

5. cpg-grep "calls:función_principal"
   → Ver quién más depende de esto

6. cpg-grep "bug:dead_branches file:<archivo>"
   → Verificar calidad antes de entregar

7. deltagraph <TASK-XXX>
   → Contexto completo si voy a implementar/modificar una tarea
```

### 5.2 Reglas para el agente

```
1. NO uses grep — usa cpg-grep
2. NO leas archivos completos — usa cpg-read
3. NO leas plan.json + ckg.json separado — usa deltagraph
4. Siempre consulta el CKG antes de implementar:
   ckg-query "type:convention scope:<módulo>"
   ckg-query "type:decision scope:<módulo>"
   ckg-query "type:warning scope:<módulo>"
5. Si descubres algo nuevo, documéntalo en el CKG
6. Si encuentras algo obsoleto, márcalo como stale
```

---

## 6. Arquitectura Interna

### 6.1 CPG Builder (`cpg.py`)

```
CPGBuilder(project_dir)
  │
  ├── build()           → sg outline + parse → cpg.json
  ├── update()          → incremental (git diff + sg outline)
  ├── scan_cfg(paths)   → CFG analysis + function-level CALLS
  ├── scan_dfg(paths)   → DFG analysis + security + secrets
  └── stats()           → métricas del grafo

Almacenamiento: .cognitive/projects/delta-skill/cpg.json
```

### 6.2 CKG (`ckg/`)

```
CodeKnowledgeGraph(project_dir)
  │
  ├── CodeGraph         → networkx.DiGraph wrapper
  │   ├── add_node/edge/get_node/neighbors/subgraph
  │   └── validate_integrity() → detección de ciclos
  │
  ├── JsonRepository    → persistencia
  ├── context_for_files() → resumen para el LLM
  ├── detect_issues()   → integridad del grafo
  └── migrate_from_facts() → migración desde facts.json

Almacenamiento: .cognitive/projects/delta-skill/ckg.json
```

### 6.3 DeltaGraph (`graph_utils.py`)

```
DeltaGraph(chain_dir)
  │
  ├── _load()           → carga plan.json + cpg.json + ckg.json
  ├── _build()          → construye networkx.DiGraph federado
  ├── neighbors()       → adyacencias en el grafo federado
  ├── path()            → camino entre dos nodos
  ├── subgraph()        → filtrado por tipo/status
  ├── unified_context() → texto formateado para el LLM
  └── export_data()     → serialización del grafo
```

### 6.4 Cache

```
_cpg_cache: dict[str, dict]
  │
  ├── <cpg_path>              → cpg.json crudo
  ├── <cpg_path>::indexes     → outgoing, incoming, calls_set (pre-indexados)
  └── <ckg_path>::ckg         → by_type, by_scope (pre-indexados)

Se invalida con: cpg-build
```

---

## 7. Comparativa CPG vs CKG

| Aspecto | CPG | CKG |
|---------|-----|-----|
| **Qué contiene** | Código fuente estructurado | Conocimiento y decisiones |
| **Se genera** | Automáticamente (sg outline + AST) | Manualmente (LLM/usuario) |
| **Actualización** | `cpg-build` | `ckg-query` + API |
| **Nodos** | 500-10000+ (archivos, funciones) | 100-500 (decisiones, convenciones) |
| **Edges** | CALLS, CONTAINS, IMPORTS | DEPENDS_ON, IMPLEMENTS, BELONGS_TO |
| **Para qué lo usa el LLM** | Entender qué hace el código | Entender por qué se hizo así |
| **Tipo de respuesta** | "set_task_status escribe a task_states" | "Decisión: step() como entry point" |
| **Detección de bugs** | CFG/DFG automático | No aplica |
| **Detección de seguridad** | Taint paths automático | Patrones de seguridad documentados |
| **Aprendizaje del proyecto** | Arquitectura, dependencias | Reglas, decisiones, advertencias |
| **Stale detection** | No aplica (el código es la verdad) | `detect_stale_nodes()` |

---

## 8. Beneficios para el LLM

### 8.1 No leer código crudo

| Antes | Después |
|-------|---------|
| `Read state.py` → 331 líneas de texto plano | `cpg-read state.py` → 11 funciones con firmas e imports |
| `grep -r "set_task_status" *.py` → 15 resultados sin contexto | `cpg-grep "contains:set_task_status"` → 1 resultado con relación exacta |
| Leer plan.json + ckg.json + código por separado | `deltagraph TASK-013` → todo integrado |

### 8.2 Entender el "por qué"

```
Sin CKG:
  "Hay una función complete_task en state.py. Llama a set_task_status."

Con CKG:
  "Hay una función complete_task en state.py. 
   La decisión TASK-005 determinó que complete_task debe usar set_task_status.
   La convención dice: 'Toda tarea completada debe registrar el cambio en task_states'.
   Y hay un warning: 'No llamar a set_task_status directo sin pasar por complete_task'."
```

### 8.3 No repetir errores

```
Sin CKG:
  El LLM implementa algo... y repite el mismo error que ya se corrigió en TASK-030.

Con CKG:
  ckg-query "type:decision status:reverted"
  → "Usar pickle → REVERTIDO por TASK-030 (inseguro)"
  → El LLM sabe que NO debe usar pickle.
```

---

## 9. Roadmap

| Capacidad | Estado | Prioridad |
|-----------|--------|-----------|
| CPG estructural (sg outline) | ✅ | Alta |
| CFG + function-level CALLS | ✅ | Alta |
| DFG + taint paths | ✅ | Alta |
| Security queries | ✅ | Alta |
| Hardcoded secrets detection | ✅ | Media |
| CKG type/scope queries | ✅ | Alta |
| CKG neighbors | ✅ | Media |
| DeltaGraph unified context | ✅ | Alta |
| Cache de índices | ✅ | Media |
| Briefing automático pre-tarea | ❌ | Alta |
| CKG + CPG cross-reference trace | ❌ | Media |
| Auto-populate CKG desde LLM | ❌ | Media |
| Security patterns en CKG | ❌ | Baja |
| Query federada (CPG + CKG) | ❌ | Baja |
| MCP server persistente | ❌ | Media |

---

## 10. Referencias

- `walker.py` — entry point de todos los comandos
- `cpg.py` — CPG Builder (sg outline + CFG + DFG)
- `ckg/` — Code Knowledge Graph (domain, application, infrastructure)
- `graph_utils.py` — DeltaGraph unificado
- `_walker/commands.py` — implementación de `cmd_cpg_grep`, `cmd_cpg_read`, `cmd_ckg_query`
- `data/sources.json` — fuentes de taint para seguridad
- `data/sinks.json` — sinks de taint para seguridad
