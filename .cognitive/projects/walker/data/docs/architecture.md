# Delta Skill — Code Knowledge Graph Architecture

> Documento generado de la sesión de diseño 2026-07-22.
> Captura las decisiones arquitectónicas, modelo de datos y plan de implementación.

---

## 🎯 Goal

Dotar al skill delta de un **sistema de conocimiento ingenieril** que:
1. Persista entre sprints (nivel project-id, no sprint)
2. Reemplace `facts.json` (lista plana → grafo con relaciones)
3. Capture trabajo fuera-de-scope como tareas `type: outside_scope`
4. Enriquezca el contexto del LLM con arquitectura + código + convenciones antes de cada tarea
5. Permita al LLM no "empezar de cero" en cada sprint

---

## 🧬 Arquitectura: Triple Grafo Unificado

```
┌─────────────────────────────────────────────────────────┐
│                    DELTA GRAPH                            │
│  Alcance: sprint activo                                  │
│  Nodos: tasks, predicates, files                         │
│  Edges: has_predicate, impacts_file, depends_on          │
│  Archivo: plan.json + progress.json                      │
│  Vida: dura un sprint                                    │
└────────────────────────┬────────────────────────────────┘
                         │
              comparten clave: ruta del archivo
                         │
┌────────────────────────┼────────────────────────────────┐
│                        │                                 │
│  ┌─────────────────────▼──────────────────────────────┐ │
│  │                  CPG (Code Project Graph)           │ │
│  │  Origen:    sg outline --json (ast-grep)           │ │
│  │  Nodos:     files, classes, functions, imports     │ │
│  │  Edges:     CONTAINS, HAS_MEMBER, IMPORTS, EXPORTS │ │
│  │  Archivo:   .cognitive/projects/{pid}/cpg.json     │ │
│  │  Actualiza: cada DESIGN.GATHER (regenerable)       │ │
│  └─────────────────────┬──────────────────────────────┘ │
│                        │                                 │
│              bridge: CPG→CKG edges manuales              │
│                        │                                 │
│  ┌─────────────────────▼──────────────────────────────┐ │
│  │                CKG (Code Knowledge Graph)           │ │
│  │  Origen:    LLM + decisiones de diseño              │ │
│  │  Nodos:     patterns, conventions, decisions,       │ │
│  │             layers, components, apis, events,       │ │
│  │             dataStructures                          │ │
│  │  Edges:     BELONGS_TO, IMPLEMENTS, FOLLOWS,        │ │
│  │             DECIDED_IN, HAS_QUALITY, EXPORTS        │ │
│  │  Archivo:   .cognitive/projects/{pid}/ckg.json     │ │
│  │  Actualiza: DESIGN.GATHER + REFINE.CKG_UPDATE       │ │
│  └────────────────────────────────────────────────────┘ │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### Clave de unión: `file` path

Los tres grafos comparten la ruta del archivo como clave de unión. Esto permite queries federadas:

```python
# "Dame todo lo relevante para engine.py"
unified_context("engine.py") → {
  "delta":   ["TASK-006 impacts engine.py", "TASK-005b depends on it"],
  "cpg":     ["class _show_on_entry_message", "def execute_sub_state_action", 
              "imports: subprocess, json, Template"],
  "ckg":     ["layer: workflow", "follows: decision-007 (inyeccion contexto)",
              "consumes: api-001 (CKG API)"]
}
```

---
## 🔄 Ciclo de Vida CPG

### Principio

El **Code Project Graph** refleja la estructura real del código. Como el código cambia constantemente, el CPG:
- **NUNCA** se regenera completo salvo la primera vez
- **SIEMPRE** se actualiza incrementalmente: solo archivos modificados
- **DETECTA** cambios automáticamente al entrar a nodos del workflow que necesitan contexto fresco

### Formato extendido de `cpg.json`

```json
{
  "version": "1.0",
  "project_id": "delta-skill",
  "generated_by": "sg outline --json --items all --view expanded",
  "generated_at": "2026-07-22T10:00:00Z",
  "updated_at": "2026-07-22T10:00:00Z",
  "update_count": 3,
  "tracked_files": {
    ".claude/skills/delta/scripts/engine.py": {
      "scanned_at": "2026-07-22T10:00:00Z",
      "checksum": "sha256:abc123..."
    }
  },
  "nodes": { ... }
}
```

Los campos `tracked_files` almacenan por cada archivo:
- `scanned_at`: timestamp del último scan
- `checksum`: SHA-256 del contenido (para detectar cambios sin depender solo de timestamp)

### Fases del CPG

| Fase | Cuándo | Acción | Mecanismo | Alcance |
|------|--------|--------|-----------|---------|
| **CREATE** | Primera ejecución del workflow en el proyecto | `sg outline --json` sobre todo el proyecto, guarda `generated_at` y pobla `tracked_files` | Subprocess `sg outline --json --items all --view expanded` | 100% archivos |
| **UPDATE** | Al entrar a DESIGN.GATHER o ANALYZE.GATHER | Detectar cambios desde `updated_at`, re-escanear solo archivos modificados, eliminar nodos de archivos borrados | Algoritmo `detect_changed_files()` (ver abajo) | Solo archivos changed/new/deleted |
| **REFRESH** | On-demand (ej: `delta --refresh-cpg`) | Regeneración completa | Ídem CREATE | 100% archivos |
| **PRUNE** | Automático en UPDATE | Eliminar nodos CPG y bridge edges de archivos que ya no existen | `remove_file_nodes(file_path)` | Archivos deleted |

### Algoritmo de detección de cambios

```
detect_changed_files(cpg_path, project_root) → List[Path]:
    1. try:
         # Estrategia primaria: git diff
         since = cpg.updated_at
         result = git diff --name-only since..HEAD
         if result.returncode == 0:
             return parse_lines(result.stdout)
       except (FileNotFoundError, subprocess error):
         pass  # git no disponible, fallback

    2. # Estrategia fallback: mtime + checksum
       changed = []
       for file_path, tracked_info in cpg.tracked_files:
           mtime = os.path.getmtime(file_path)
           if mtime > parse_time(tracked_info.scanned_at):
               actual_checksum = sha256(file_path)
               if actual_checksum != tracked_info.checksum:
                   changed.append(file_path)
       # Detectar archivos nuevos (existen en disco, no en tracked_files)
       all_files = glob project recursively
       for f in all_files:
           if f not in cpg.tracked_files:
               changed.append(f)
       # Detectar archivos borrados (existen en tracked_files, no en disco)
       for f in cpg.tracked_files:
           if not os.path.exists(f):
               changed.append(f)  # Marcar para eliminar
       return changed
```

### Actualización incremental del CPG

```
update_cpg(cpg_path, changed_files, project_root):
    cpg = load_json(cpg_path)
    
    # 1. Eliminar nodos de archivos borrados
    for f in changed_files:
        if not os.path.exists(f):
            remove_all_nodes_for_file(cpg, f)
    
    # 2. Re-escanear archivos modificados/nuevos
    modified = [f for f in changed_files if os.path.exists(f)]
    if modified:
        result = subprocess.run(
            ["sg", "outline", "--json", "--items", "all", "--view", "expanded"] + modified
        )
        new_nodes = parse_sg_outline(result.stdout)
        for f in modified:
            remove_all_nodes_for_file(cpg, f)     # reemplazo quirúrgico
            cpg.tracked_files[f] = {
                "scanned_at": now(),
                "checksum": sha256(f)
            }
        cpg.nodes.update(new_nodes)
    
    # 3. Actualizar metadata
    cpg.updated_at = now()
    cpg.update_count += 1
    save_json(cpg_path, cpg)
```

### Cuándo se actualiza el CPG

| Workflow Node | Al Entrar | CPG Action | Rationale |
|--------------|-----------|------------|-----------|
| **DESIGN.GATHER** | ✅ Sí | 🔄 UPDATE | El LLM necesita estructura actual del código antes de decidir arquitectura |
| **ANALYZE.GATHER** | ✅ Sí | 🔄 UPDATE | El contexto inyectado debe reflejar el estado real del código |
| **IMPLEMENT.WORK** | ❌ No | — | El código está en flujo de cambio; el CPG se desincronizaría constantemente |
| **REFINE.DIAGNOSE** | ✅ Sí | 🔄 UPDATE | Necesario para detectar deuda, código huérfano, referencias rotas |
| **REFINE.CKG_UPDATE** | ❌ No | — | Solo actualiza CKG, no necesita CPG fresco |

---

## 🔄 Ciclo de Vida CKG (refinado)

### Principio

El CKG es **manual + LLM**. Nunca se regenera automáticamente. Se enriquece en momentos específicos del workflow.

| Fase | Cuándo | Acción | Mecanismo |
|------|--------|--------|-----------|
| **CREATE** | DESIGN.GATHER (primera vez del proyecto) | LLM extrae conocimiento de su memoria de conversación → escribe ckg.json | CKG extraction prompt |
| **ENRICH** | DESIGN.GATHER (siguientes sprints) | LLM añade nuevas decisiones, patrones, convenciones | Conversación actual + diff con CKG anterior |
| **CORRECT** | REFINE.CKG_UPDATE | LLM corrige errores, enriquece nodos existentes, marca `deprecated` | Aprendizajes de la implementación |
| **OUTSIDE** | REFINE.CKG_UPDATE | Tareas OUTSIDE-XXX se convierten en nodos CKG | `plan.json` → CKG |
| **DEPRECATE** | REFINE.DIAGNOSE | Si un archivo referenciado en CKG ya no existe, marcar nodo como `stale: true` | Detección de archivos desaparecidos |

### ⚠️ Regla: DeltaGraph NUNCA persiste

DeltaGraph es una **vista on-demand**, no una fuente de verdad. Se construye en memoria (`nx.DiGraph`) desde los 4 archivos fuente:
- `plan.json` + `progress.json` (datos del sprint)
- `cpg.json` + `ckg.json` (datos del proyecto)

**No existe `delta-graph.json` ni equivalente.** Razones:
1. Las fuentes de verdad ya persisten — duplicar crea riesgo de sync
2. Siempre fresco — si cpg.json se regenera, DeltaGraph lo toma al instante
3. Sin deuda de sync — 0 archivos extra que mantener consistentes
4. Regenerable — mismo input → mismo grafo, siempre

---

## 📐 Modelo de Datos

### CPG — Code Property Graph (auto-generado + AST analysis)

El CPG es un **Code Property Graph unificado**: combina la estructura del código (sg outline) con el **flujo de control (CFG)** y el **flujo de datos (DFG)** en un solo grafo. Cada capa se construye con herramientas distintas pero comparte el mismo espacio de nodos.

#### Capa 1: Estructural (sg outline)

| Node Type | Descripción | Ejemplo | Fuente |
|-----------|-------------|---------|--------|
| `file` | Archivo fuente | `engine.py` → 39 items | sg outline |
| `class` | Clase Python | `DeltaGraph` → 11 métodos | sg outline |
| `function` | Función top-level | `cmd_submit()` | sg outline |
| `method` | Método de clase | `DeltaGraph.neighbors()` | sg outline |
| `module` | Import | `import networkx as nx` | sg outline |
| `constant` | Constante/variable global | `NODE_TASK = "task"` | sg outline |

| Edge Type | De → A | Significado |
|-----------|--------|-------------|
| `CONTAINS` | file → class/function/constant | Pertenencia a archivo |
| `HAS_MEMBER` | class → method | Método de clase |
| `IMPORTS` | file → module | Dependencia de import |

#### Capa 2: Flujo de Control (CFG) — AST analysis

Se genera para cada función/método analizando el AST con `ast` module (Python) o tree-sitter (multi-lenguaje).

| Node Type | Descripción | Atributos |
|-----------|-------------|-----------|
| `statement` | Statement genérico (assign, expr, pass, etc.) | `astKind`, `line`, `column`, `code_snippet` |
| `condition` | Punto de bifurcación (if/elif/match) | `astKind`, `line`, `is_terminal` (if sin else) |
| `branch` | Rama de ejecución (then, else, case-N) | `label`, `is_default` (match default) |
| `loop` | Constructo de ciclo (for/while) | `astKind`, `line`, `has_break`, `has_continue`, `is_infinite` |
| `return_stmt` | Return statement | `line`, `has_value`, `value_type` |

| Edge Type | De → A | Significado | Caso de uso |
|-----------|--------|-------------|-------------|
| `FLOWS_TO` | statement → statement | Orden de ejecución secuencial | Path traversal |
| `BRANCHES_TO` | condition → branch | Condición deriva a una rama (then/else) | Dead branch |
| `CONDITION_OF` | expression → condition | Expresión es la condición de un if/match | Branch analysis |
| `BODY_OF` | statement → function/loop | Statement pertenece al cuerpo | Scope |
| `LOOP_BODY` | loop → statement | Statement está dentro del cuerpo del loop | Infinite loop |
| `BREAKS_FROM` | break → loop | Break statement sale del loop | Infinite loop |
| `EXCEPTION_FLOW` | try → except handler | Flujo excepcional desde try a except | Missing return |
| `ENTERS_FUNCTION` | function → statement | Punto de entrada de función | CFG entry |
| `EXITS_FUNCTION` | return_stmt → function | Punto de salida de función | Missing return |

**Detecciones vía CFG:**
| Análisis | Patrón de consulta |
|----------|-------------------|
| Rama muerta | `condition -[:BRANCHES_TO]-> branch WHERE branch no tiene path a EXITS_FUNCTION` |
| Bucle infinito | `loop(has_break=false) -[:BODY_OF]-> (s) -[:FLOWS_TO*]-> loop` (sin salida) |
| Función sin return | `function -[:ENTERS_FUNCTION]-> (e) WHERE NOT EXISTS path from e to EXITS_FUNCTION` |

#### Capa 3: Flujo de Datos (DFG) — AST analysis + reaching definitions

| Node Type | Descripción | Atributos |
|-----------|-------------|-----------|
| `variable` | Definición o uso de variable | `name`, `scope`, `is_initialized`, `type_hint`, `line_def`, `line_uses` |
| `sink` | Sumidero sensible (seguridad) | `kind` (sql, response, file, log, eval), `api` (`db.execute`, `send()`) |
| `source` | Fuente no confiable | `kind` (http_request, file_read, user_input, env_var) |

| Edge Type | De → A | Significado | Caso de uso |
|-----------|--------|-------------|-------------|
| `DEFINES` | statement → variable | Statement define una variable | Variable tracking |
| `READS` | statement → variable | Statement lee una variable | Usage tracking |
| `DATA_FLOW` | variable → variable | Propagación de datos (def→use) | Taint tracking |
| `REACHES` | variable → sink | Data reaches sensitive sink | SQL injection, XSS |
| `COMES_FROM` | variable → source | Variable origina en fuente no confiable | Security analysis |
| `CALL_ARG` | call → variable | Variable pasa como argumento a una llamada | Taint through calls |
| `ATTRIBUTE_OF` | derived_var → base_var | Acceso a atributo (r.stdout → r) | Data gap, taint chain |
| `INSTANCE_OF` | variable → type/class | Variable es instancia de un tipo | Data gap detection |
| `SCOPE_OF` | variable → function/class | Ámbito de la variable | Scoping |

**Detecciones vía DFG:**
| Análisis | Patrón de consulta |
|----------|-------------------|
| SQL Injection | `source -[:DATA_FLOW*]-> variable -[:CALL_ARG]-> call -[:REACHES]-> sink(kind=sql)` |
| Exposición datos sensibles | `sink(kind=response) ←[:REACHES]- call ←[:CALL_ARG]- variable ←[:DATA_FLOW*|ATTRIBUTE_OF]- source(kind=user_input)` |
| Variable no inicializada | `variable(is_initialized=false) ←[:READS]- statement` |
| Atributo inexistente | `variable -[:INSTANCE_OF]-> type WHERE attr ∉ type.fields` (data gap) |
| Command injection | `source -[:DATA_FLOW*|CALL_ARG]-> call -[:REACHES]-> sink(kind=subprocess)` |

#### Resumen: todas las capas unificadas en CPG

```
┌──────────────────────────────────────────────────────────────┐
│                      CPG                                      │
├───────────────────┬──────────────────┬──────────────────────┤
│   ESTRUCTURAL     │   CFG            │   DFG                 │
│   (sg outline)    │   (ast)          │   (ast)               │
│                   │                  │                        │
│  file ──→ class   │  function ─→ stmt│  stmt ─→ var          │
│  class ─→ method  │  condition→branch│  var ─→ var           │
│  file ─→ import   │  stmt ─→ stmt   │  var ─→ sink          │
│  call ─→ arg      │  stmt ─→ return  │  source→var           │
│                   │  loop → body     │  call → arg           │
│                   │  break → loop    │  attr → base          │
│                   │  try → except    │  var → type           │
└───────────────────┴──────────────────┴───────────────────────┘
        ▲                    ▲                  ▲
        │                    │                  │
   sg outline           ast module         ast module
   (tree-sitter)        (reaching defs)    (data-flow)
```

Todas comparten el mismo `path` como join key y el mismo espacio de IDs. Un nodo `function` de la capa estructural es el mismo nodo al que se conectan los edges CFG y DFG.

### CKG — Manual + LLM

| Node Type | Descripción | ID Format |
|-----------|-------------|-----------|
| `decision` | ADR (Architecture Decision Record) | `decision-NNN` |
| `pattern` | Patrón de diseño o arquitectura | `pattern-NNN` |
| `convention` | Regla de estilo o convención | `convention-NNN` |
| `layer` | Capa arquitectónica | `layer-NNN` |
| `component` | Componente del sistema | `component-NNN` |
| `api` | API pública | `api-NNN` |
| `event` | Evento del workflow | `event-NNN` |
| `dataStructure` | DTO, schema, formato de datos | `dataStructure-NNN` |
| `class` | Clase (planeada o existente) | `class-NNN` |
| `function` | Función clave (API pública) | `function-NNN` |
| `module` | Módulo/paquete | `module-NNN` |
| `quality` | Métricas de calidad | `quality-NNN` |

| Edge Type | Significado |
|-----------|-------------|
| `BELONGS_TO` | Entidad → capa o módulo |
| `IMPLEMENTS` | Clase → patrón |
| `FOLLOWS` | Archivo/clase → convención o decisión |
| `DECIDED_IN` | Patrón/convención → decisión (ADR) |
| `CONSUMES` | Función → API |
| `EXPORTS` | Función → API |
| `TRIGGERS` | Función → evento |
| `HANDLED_BY` | Evento → función |
| `EXTENDS` | Decisión → decisión (herencia) |
| `SUPERSEDES` | Decisión → decisión (reemplazo) |
| `RELATED_TO` | Componente → componente |
| `INSTANCE_OF` | DataStructure → patrón |

### Bridge CPG → CKG

Edges que conectan entidades reales del código (CPG) con conocimiento arquitectónico (CKG):

| Edge Type | Significado | Ejemplo |
|-----------|-------------|---------|
| `IMPLEMENTS` | CPG.class → CKG.pattern | `DeltaGraph → pattern-003` |
| `FOLLOWS` | CPG.file/function → CKG.decision | `engine.py → decision-007` |
| `BELONGS_TO` | CPG.file/function → CKG.layer | `advance.py → layer-002` |
| `EXPORTS` | CPG.function → CKG.api | `run_analysis → api-002` |

---

## 🔄 Workflow: Dónde se usa cada grafo

| Sub-estado | DeltaGraph | CPG | CKG | Acción |
|-----------|------------|-----|-----|--------|
| **DESIGN.GATHER** | — | 🔄 UPDATE (incremental) | 🟢 crear/enriquecer | Al entrar: detect_changed_files() → sg outline parcial. Luego LLM extrae → ckg.json |
| **ANALYZE.GATHER** | 🔵 tareas + archivos | 🔵 estructura código | 🔵 decisiones + patrones | contexto inyectado en on_entry_message |
| **IMPLEMENT.WORK** | 🔵 archivos impactados | 🔵 clases/funciones | 🔵 convenciones activas | LLM implementa siguiendo reglas. CPG no se actualiza (código en flujo) |
| **IMPLEMENT.CODE_REVIEW** | 🔵 dependencias | 🔵 cambios en estructura | 🔵 compliance check | ¿sigue las convenciones? |
| **VERIFY.QA** | 🔵 predicates | 🔵 cobertura | 🔵 trazabilidad | ¿todos los predicates cubiertos? |
| **REFINE.DIAGNOSE** | 🔵 deuda sprint | 🔵 código huérfano | 🔵 issues + deuda | ¿qué está desconectado? |
| **REFINE.CKG_UPDATE** | 🔵 OUTSIDE-XXX | — | 🟢 corregir/enriquecer | LLM extrae aprendizajes → ckg.json |

---

## 📋 Decisiones Arquitectónicas (ADRs)

| ID | Decisión | Rationale |
|----|----------|-----------|
| ADR-001 | CKG reemplaza facts.json | facts.json era lista plana sin relaciones |
| ADR-002 | Nuevo nodo DESIGN en workflow | Separar diseño de implementación |
| ADR-003 | CKG persiste a nivel project-id | Conocimiento sobrevive al sprint |
| ADR-004 | Actualización manual (LLM decide) | El LLM tiene contexto completo de decisiones |
| ADR-005 | Outside-scope como type:outside_scope | Capturar aprendizaje de trabajo no planeado |
| ADR-006 | Bridge DeltaGraph↔CKG via archivos | Unir trazabilidad de sprint con arquitectura |
| ADR-007 | Inyección por distancia de saltos (depth 1-4) | No saturar al LLM con todo el grafo |
| ADR-008 | CPG como capa de código auto-generada | sg outline provee estructura real del código |
| ADR-009 | Triple grafo unificado via archivos | Query federada para contexto completo |
| ADR-010 | sg outline como fuente única de verdad | Multi-lenguaje, tree-sitter, estructura estándar |
| ADR-011 | CPG se actualiza incrementalmente (solo archivos modificados) | El código cambia constantemente; regenerar todo es ineficiente. Git diff (primario), mtime+checksum (fallback) |
| ADR-012 | CPG se actualiza al entrar a DESIGN.GATHER y ANALYZE.GATHER | El LLM necesita estructura fresca antes de decisiones de diseño o análisis. No se actualiza durante IMPLEMENT |
| ADR-013 | CPG unificado incluye CFG + DFG como capas del mismo grafo | Un solo grafo consultable para estructura, control flow y data flow. AST analysis (ast module + tree-sitter) genera las capas CFG/DFG. sg outline provee la capa estructural |

---

## 🗂️ Estructura de Archivos del Proyecto

```
.cognitive/projects/delta-skill/
├── project.json              ← perfil del proyecto
├── workflow.json             ← definición del grafo de workflow
├── cpg.json                  ← Code Project Graph (auto-generado)
├── ckg.json                  ← Code Knowledge Graph (manual + LLM)
├── facts.json                ← [OBSOLETO] migrado a ckg.json
├── facts.json.migrated       ← backup post-migración
└── delta/
    └── ckg-knowledge-graph-20260722/
        ├── plan.json         ← 9 tareas del sprint actual
        ├── progress.json
        └── sprint-state.json
```

---

## 📦 Módulo CKG: Arquitectura DDD

El Code Knowledge Graph se implementa como un módulo DDD con 3 capas + schema. SRP: cada archivo = una responsabilidad. SSOT: `domain/models.py` define únicos tipos válidos. KISS: 9 archivos, sin DI containers ni event bus.

```
scripts/ckg/
├── __init__.py                          # Public API: from ckg import CodeKnowledgeGraph
├── domain/                              # Core puro, sin IO
│   ├── models.py                        # Node dataclass + Edge dataclass + NodeType/EdgeType Enum
│   ├── errors.py                        # CkgError, NodeNotFound, EdgeNotFound, ValidationError
│   └── graph.py                         # CodeGraph: wrapper tipado sobre nx.DiGraph
├── application/                         # Orquestación, casos de uso
│   ├── ckg.py                           # CodeKnowledgeGraph: facade (save, load, add_node, add_edge)
│   ├── context.py                       # ContextService: context_for_files(depth), context_for_task
│   ├── detection.py                     # DetectionService: detect_issues, detect_stale_nodes
│   └── migration.py                     # MigrationService: migrate_from_facts
├── infrastructure/                      # IO, frameworks, external
│   ├── repository.py                    # CkgRepository: save/load JSON + formato canónico
│   ├── schema.py                        # SchemaValidator: contra ckg.schema.json + integridad
│   ├── scanners.py                      # ImportScanner, LayerScanner (desde AST real)
│   └── qlty.py                          # QltyMetricsClient: coverage, complexity, churn
└── schema/
    └── ckg.schema.json                  # JSON Schema draft-07 del formato ckg.json
```

### Principios

| Principio | Aplicación |
|-----------|-----------|
| **SRP** | `models.py` define tipos, `graph.py` opera el grafo, `repository.py` hace IO, `scanners.py` escanea AST |
| **SSOT** | `domain/models.py` es el ÚNICO lugar donde se definen NodeType y EdgeType. `ckg.schema.json` se genera/testea contra los Enums |
| **KISS** | Sin interfaces abstractas (Python duck typing). Sin DI containers. Sin event bus. Cada servicio recibe dependencias por constructor simple |
| **DDD Entity** | `Node` tiene identidad (node_id), `Edge` tiene identidad compuesta (from_id + to_id + type) |
| **DDD Aggregate** | `CodeGraph` es el aggregate root. Todas las operaciones pasan por él |
| **DDD Repository** | `CkgRepository` persiste/recupera el aggregate. El dominio no sabe de JSON |
| **DDD Domain Service** | `ContextService`, `DetectionService` operan sobre el grafo con lógica que no cabe en una entidad |
| **Schema enforcement** | `SchemaValidator` corre en post-load: si el JSON no pasa schema o integrity, se rechaza |

### Flujo de creación de un CKG

```
new_node = Node(id="decision-042", type=NodeType.DECISION, label="ADR: ...")
ckg.add_node(new_node)              # domain/graph.py — CodeGraph.add_node()
ckg.save()                          # infrastructure/repository.py — CkgRepository.save()
                                    #   → serializa CodeGraph a dict
                                    #   → valida contra ckg.schema.json via SchemaValidator
                                    #   → escribe .cognitive/projects/{pid}/ckg.json
```

### Flujo de carga

```
ckg = CodeKnowledgeGraph.load(project_dir)  # facade application/ckg.py
  → CkgRepository.load()                     # infrastructure/repository.py
    → SchemaValidator.validate()             # post-load: schema + integridad
    → construye CodeGraph con Node/Edge objects  # domain/graph.py
```

---

## 🏗️ Walker como API de Sprint (Single Source of Truth)

### Principio
El walker (`walker.py` + `spring.py`) es la **UNICA interfaz** para modificar el sprint. Ningún agente edita `plan.json`, `progress.json` o `ckg.json` directamente. Toda operación de refinamiento de sprint debe pasar por comandos del walker.

### Anti-pattern detectado
Editar `plan.json` directamente desde el LLM → **salta validación de schema, no genera trail, puede corromper el plan, no hay enforcement de reglas de negocio**. Las operaciones de refinamiento de sprint (agregar/partir/reordenar tareas, actualizar metadata, ajustar predicates) son actividades legítimas de Scrum que el walker debe soportar como API.

### Acciones actuales del walker API

| Acción | Comando | Estado |
|--------|---------|--------|
| Agregar 1 tarea | `spring.py add-task` | ✅ Existe |
| Actualizar campo de tarea | `spring.py update-task` | ✅ Existe |
| Actualizar goal + predicates | `spring.py update-goal` | ✅ Existe |
| Bulk update | `spring.py bulk-update` | ✅ Existe |

### Gaps identificados (requerimientos faltantes)

| Acción | Descripción | Prioridad |
|--------|-------------|-----------|
| `update-metadata` | Actualizar `sprint_goal`, `expected_result`, `roles`, `sprint_scope` (included/excluded) | Alta |
| `split-task` | Partir una tarea existente en N sub-tareas con auto-generación de task_ids, depends_on automáticos, y rebalanceo de curvature | Alta |
| `batch-add` | Agregar múltiples tareas relacionadas en 1 solo llamado, con depends_on explícitos | Alta |
| `reorder-tasks` | Reordenar la secuencia de ejecución manteniendo integridad del grafo de dependencias | Media |
| `remove-task` | Eliminar una tarea y re-conectar dependencias de sus dependientes al nodo anterior | Media |
| `validate-plan` | Ejecutar todos los predicates de `g_objetivo.must_exist` contra el plan completo + reportar gaps | Media |
| `update-predicates` | Actualizar `validation.predicates` de una tarea (hoy requiere JSON escapado en CLI) | Baja |

### Reglas de enforcement
1. Toda escritura a `plan.json`/`progress.json` pasa por `spring.py` (schema validation + trail)
2. `add-task` auto-genera `task_id` secuencial — no se permiten task_ids manuales
3. `update-task` solo modifica campos en `_EDITABLE_TASK_FIELDS` — campos protegidos (task_id, created_at, etc.) son inmutables
4. `split-task` verifica que la suma de curvatures de las sub-tareas no exceda la curvature original × 1.2
5. `remove-task` verifica que ningún task dependiente quede huérfano (sus depends_on apuntan al removed)

### Referencias
- ADR-011 (CKG decision-011): Walker como API de Sprint
- Implementación: TASK-009 a TASK-013 en sprint actual

---

## 📊 Estado Actual (2026-07-22)

| Artefacto | Estado | Métricas |
|-----------|--------|----------|
| `cpg.json` (estructural) | ✅ Generado | 883 nodos, 893 edges, 45 archivos |
| `cpg.json` (CFG+DFG) | 📐 Diseñado | Pendiente implementar (TASK-001c, TASK-001d) |
| `ckg.json` | ✅ Poblado | 49 nodos (11 ADRs, 5 convenciones, 4 patrones), 64 edges |
| `ckg/` module | 📐 Diseñado (DDD) | 9 archivos: domain (3), application (4), infrastructure (4) + schema |
| Bridge CPG→CKG | ✅ 23 edges | CPG→CKG bridge edges en `ckg.json.cpg_bridge` |
| CPG Lifecycle | ✅ Definido en ADR-011 | CREATE → UPDATE incremental → PRUNE. Git diff + mtime fallback |
| CKG Lifecycle | ✅ Definido | CREATE / ENRICH / CORRECT / OUTSIDE / DEPRECATE |
| Sprint | ⏳ 0/17 tareas | TASK-001 lista para iniciar — modulo DDD con 9 archivos + schema |
| `facts.json` | ⚠️ Pendiente migrar | Se migrará en `migration.py` (TASK-001 + TASK-004) |
| Walker API (TASK-009..013) | 📐 Requerimientos documentados | Pendiente implementar |

## 🚀 Sprint: 17 Tareas (2 tracks paralelos)

### Track A — Code Intelligence (CPG + CKG + DeltaGraph)

```
TASK-001 ───────────────────────────────────────────→ TASK-001b → TASK-001c → TASK-001d → ...
  CKG module DDD                                      CPG        CPG-CFG   CPG-DFG
  domain/models.py + graph.py                         structural AST       AST+DFG
  application/{ckg,context,detection,migration}.py
  infrastructure/{repository,schema,scanners,qlty}.py
  schema/ckg.schema.json

TASK-002 → TASK-003 → TASK-004 → TASK-005 → TASK-005b → TASK-006 → TASK-007 → TASK-008
scanners   Qlty +    detect +   ckg-update  bridge     DESIGN    outside-   observability
imports+   context   migrate    script     DG↔CKG    +inject   scope
layers     depth     facts
```

### Track B — Walker API (infraestructura del sprint)

```
TASK-009 (update-metadata) ──→ TASK-010 (split-task) ──→ TASK-011 (batch-add)
                                                              │
                                                              └──→ TASK-012 (reorder/remove)
                                                                    │
                                                                    └──→ TASK-013 (validate-plan)
```

---

## ⚙️ Workflow Engine v2 — Arquitectura Event-Driven

> Aprobado en sprint `workflow-engine-v2-20260723`. Diseño registrado en CKG (nodos module-005 a module-010, pattern-005, decision-012, layer-004).

### Diagnóstico (G_actual)

El problema actual es acoplamiento y falta de abstracciones:

| Archivo | Problema |
|---------|----------|
| `commands.py` (1474 lines) | Monolito: CLI + orquestación + persistencia + gates + CKG + backlog |
| `engine.py` (888 lines) | Duplica helpers de estado y lógica de ejecución |
| `utils.py` | Invocado directamente desde todos lados |
| `progress.json` | Ghost dependency: aparece en health, backlog, status |
| `find_workflow()` | Migración inline + fallback emergency |

No hay engine puro, eventos declarativos, contratos formales ni replay.

### Arquitectura propuesta (G_objetivo)

```
┌──────────────────────────────────────────┐
│              LLM / User                    │
│  walker.py CLI (thin shell, ~200 lines)   │
├──────────────────────────────────────────┤
│         workflow_engine.py (puro)         │
│  - validate()  ← workflow.json + state    │
│  - transform() ← transiciones con efecto  │
│  - execute()   ← sub-estados mode:auto    │
│  - persist()   ← sprint-state.json only   │
│  - replay()    ← event sourcing           │
├──────────────────────────────────────────┤
│  Scheduler    │  Events    │  Contracts   │
│  (DAG)        │  (on:)     │  (schemas)   │
├──────────────────────────────────────────┤
│  spring.py   task.py   ckg.py   cpg.py   │
└──────────────────────────────────────────┘
```

### 6 Módulos

| # | Módulo | Responsabilidad | Archivos | CKG Node |
|---|--------|----------------|----------|----------|
| 1 | **workflow_engine.py** | Core puro sin IO: validate(), transform(), execute(), persist() | `_walker/workflow_engine.py` | module-005 |
| 2 | **Event System** | Triggers declarativos on:{} en workflow.json | `_walker/events.py` + schema | module-006 |
| 3 | **Job Scheduler** | Paralelismo DAG entre tareas independientes | `_walker/scheduler.py` | module-007 |
| 4 | **Formal Contracts** | Schema JSON por sub-estado, validación automática | `_walker/contracts.py`, `schema/contracts/` | module-008 |
| 5 | **Idempotent Persistence** | Snapshot/restore, eliminar progress.json | `_walker/persistence.py` | module-009 |
| 6 | **Event Sourcing** | event_log[] replayable, workflow_engine.replay() | `_walker/event_store.py` | module-010 |

### Event/Trigger System (GitHub Actions del delta)

```json
{
  "on": {
    "task_created": { "run": "python3 scripts/notify.py --task $TASK_ID" },
    "qa_failed": [
      { "run": "python3 scripts/auto-diagnose.py" },
      { "transition": "REFINE" }
    ],
    "sprint_closed": { "run": "python3 scripts/archive-metrics.py" }
  }
}
```

### Plan de Migración

| Fase | Qué | Depende de |
|------|-----|-----------|
| **0** (✅ hecha) | Diseño de schemas, contratos, arquitectura de eventos | — |
| **1** | workflow_engine.py: extraer lógica pura de commands.py + engine.py | Fase 0 |
| **2** | events.py + scheduler.py: triggers declarativos, DAG | Fase 1 |
| **3** | contracts + persistence: schemas formales, migrar progress.json | Fase 2 |
| **4** | event sourcing: event_log[] replayable, versionado semántico | Fase 3 |

### Dependencias entre tareas del sprint

```
TASK-001 (core) → TASK-002 (events) → TASK-007 (scheduler DAG)
                                         → TASK-004 (contracts) → TASK-005 (persistence) → TASK-006 (event sourcing)
```

### Bugs de infraestructura descubiertos durante el diseño

1. **`write_json()` silencioso** (`spring/common.py`): validaba schema pero si fallaba, retornaba `None` sin escribir ni informar al caller. El caller validaba el archivo original y proseguía como si todo OK. Fix: ahora `raise RuntimeError`.
2. **`task_id` ignorado en `add-task`** (`spring/plan.py`): `cmd_add_task` siempre auto-generaba TASK-NNN ignorando el `task_id` que `plan_editor.py` pasaba en el JSON. Fix: ahora respeta el task_id del input si es válido y no duplicado.
3. **`task_context` requiere `minLength: 10`** (`schema/plan.schema.json`): todos los 6 campos de task_context necesitan mínimo 10 caracteres. Los valores cortos causaban que `write_json` rechazara la escritura.

---

*Documento generado automáticamente de la sesión de arquitectura. Última actualización: 2026-07-23.*
