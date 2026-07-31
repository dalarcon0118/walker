# Plan de Implementación: CognitiveGraph

## Fase 1: Infraestructura base

### Tarea 1.1 — Crear CognitiveGraph core
**Archivo:** `_walker/shared/cognitive_graph.py`

Implementar la clase base con:
- `register_index(name, schema)` — registrar un índice con su schema + storage
- `search(index, query)` — consultar un índice
- `search_many(queries)` — consultar múltiples índices
- `store(index, id, data)` — indexar/actualizar un documento
- `get(index, id)` — obtener documento por ID
- `delete(index, id)` — eliminar documento
- `schema(index)` — devolver mapping del índice (tipos, campos, stats)
- `metrics(index)` — devolver estadísticas del índice
- `list_indexes()` — listar índices disponibles

**Criterio de aceptación:**
```python
cg = CognitiveGraph(chain_dir)
cg.register_index("test", {"fields": {"type": "type"}, "storage": {"path": "/tmp/test.json"}})
cg.store("test", "doc-1", {"type": "decision", "statement": "test"})
assert cg.get("test", "doc-1")["_source"]["statement"] == "test"
assert cg.search("test", {"type": "decision"})["hits"]["total"] == 1
cg.delete("test", "doc-1")
assert cg.search("test", {})["hits"]["total"] == 0
```

**Dependencias:** Ninguna
**Estimación:** 1 módulo (~200 líneas)

### Tarea 1.2 — Soporte de storage (JSON/GZIP por índice)
**Archivo:** `_walker/shared/cognitive_graph.py` (mismo archivo)

Cada índice puede tener su propio storage:
- `path`: ruta al archivo de datos
- `format`: `json` | `json.gz`

Implementar load/save por índice, no global.

**Criterio:**
```python
cg = CognitiveGraph("/tmp/test_cg")
cg.register_index("test-gz", {"storage": {"path": "/tmp/test.json.gz", "format": "json.gz"}})
cg.store("test-gz", "doc-1", {"type": "decision"})
assert os.path.exists("/tmp/test.json.gz")
```

**Dependencias:** Tarea 1.1
**Estimación:** +50 líneas

### Tarea 1.3 — Schema dinámico (mapping por índice)
**Archivo:** `_walker/shared/cognitive_graph.py`

Cada índice expone su schema (derivado de los datos reales):
- Tipos disponibles con conteo
- Campos disponibles
- Stats

```python
cg.schema("ckg")
→ {"fields": {"type": "type", "scope": "scope", ...},
   "types": {"refactor": 11, "decision": 53, ...},
   "total_docs": 196}
```

**Dependencias:** Tarea 1.1
**Estimación:** +30 líneas

---

## Fase 2: Migración de índices

### Tarea 2.1 — Migrar índice CKG
**Archivos:**
- `ckg/domain/knowledge_graph.py` — actualizar para usar CognitiveGraph como storage
- `ckg/application/ckg.py` — CodeKnowledgeGraph usa CognitiveGraph.register_index("ckg")

El CKG se registra como índice "ckg" con su schema actual:
```python
CognitiveGraph.register_index("ckg", {
    "fields": {"type": "type", "scope": "scope", "task": "metadata.task_id"},
    "types": {"decision": {}, "refactor": {}, "convention": {}, ...},
    "storage": {"path": "data/ckg.json.gz", "format": "json.gz"},
    "description": "Knowledge Graph: decisiones, convenciones, patrones",
})
```

**Criterio:** `cg.search("ckg", {"type": "refactor"})` funciona. `cg.schema("ckg")` devuelve tipos correctos.

**Dependencias:** Fase 1
**Estimación:** 1 archivo (~50 líneas de cambio)

### Tarea 2.2 — Migrar índice CPG
**Archivo:** `cpg/infrastructure/repository.py`

El CPG se registra como índice "cpg":
```python
CognitiveGraph.register_index("cpg", {
    "fields": {"function": "name", "file": "path", "class": "name", "type": "type"},
    "types": {"function": {}, "class": {}, "file": {}, "module": {}},
    "storage": {"path": "data/cpg.json.gz", "format": "json.gz"},
    "description": "Code Property Graph: estructura del código fuente",
})
```

**Criterio:** `cg.search("cpg", {"function": "cmd_create"})` devuelve nodos del CPG.

**Dependencias:** Fase 1
**Estimación:** 1 archivo (~30 líneas de cambio)

### Tarea 2.3 — Migrar índice Delta
**Archivo:** a definir (hoy no hay índice delta unificado — los datos están en `delta/`)

Crear índice "delta" que indexa sprints desde los plan.json:
```python
CognitiveGraph.register_index("delta", {
    "fields": {"task": "task_id", "file": "file", "domain": "domain", "type": "type"},
    "types": {"sprint": {}, "task": {}},
    "storage": {"path": "data/delta.json.gz", "format": "json.gz"},
    "description": "DeltaGraph: tareas y sprints del proyecto",
})
```

**Criterio:** `cg.search("delta", {"domain": "core"})` devuelve tareas.

**Dependencias:** Fase 1
**Estimación:** 1 archivo nuevo (~80 líneas)

### Tarea 2.4 — Migrar índice Trace
**Archivo:** a definir (hoy los traces están en `data/traces/*.json.gz`)

Crear índice "trace":
```python
CognitiveGraph.register_index("trace", {
    "fields": {"task": "task_id", "tool": "tool", "type": "event_type"},
    "types": {"tool_call": {}, "event": {}},
    "storage": {"path": "data/trace.json.gz", "format": "json.gz"},
    "description": "TraceGraph: historial de llamadas a herramientas",
})
```

**Criterio:** `cg.search("trace", {"tool": "Write"})` devuelve eventos.

**Dependencias:** Fase 1
**Estimación:** 1 archivo nuevo (~80 líneas)

---

## Fase 3: Tasks del walker

### Tarea 3.1 — Actualizar tasks/kg.py
**Archivo:** `tasks/kg.py`

Reescribir tasks para usar CognitiveGraph directamente:
```python
def kg_search(ctx, data):
    cg = CognitiveGraph(ctx.chain_dir)
    index = data.get("index", "ckg")
    query = data.get("query", data.get("filters", {}))
    return TaskResult.success(data=cg.search(index, query))

def kg_store(ctx, data):
    cg = CognitiveGraph(ctx.chain_dir)
    cg.store(data["index"], data.get("id"), data.get("doc", data))
    return TaskResult.success(data={"ok": True})

def register(registry):
    registry.register("kg_search", kg_search)
    registry.register("kg_store", kg_store)
    registry.register("kg_schema", ...)
```

**Criterio:**
```
walker3 delta-skill/task/kg_search --index ckg --query '{"type":"refactor"}'
walker3 delta-skill/task/kg_store --index ckg --type refactor --statement "..."
```

**Dependencias:** Fase 2
**Estimación:** 1 archivo (~60 líneas de cambio)

---

## Fase 4: Reemplazar QueryManager

### Tarea 4.1 — QueryManager como wrapper de CognitiveGraph
**Archivo:** `_walker/workflow_engine/context/query_manager.py`

Reemplazar implementación interna para delegar en CognitiveGraph:
```python
class QueryManager:
    def __init__(self, chain_dir):
        self._cg = CognitiveGraph(chain_dir)
    
    def query(self, layers):
        return self._cg.search_many(layers)
    
    def query_layer(self, layer, filters):
        return self._cg.search(layer, filters)
```

**Criterio:** Todos los providers (CKGProvider, CPGProvider, etc.) siguen funcionando sin cambios.

**Dependencias:** Fase 2
**Estimación:** 1 archivo (~30 líneas de cambio)

---

## Fase 5: Limpieza

### Tarea 5.1 — Eliminar query/parser.py y query/executor.py
Los archivos `query/parser.py` y `query/executor.py` dejan de ser necesarios. Su lógica pasa a CognitiveGraph.

**Criterio:** `from query import QueryEngine` ya no existe. Todo pasa por CognitiveGraph.

**Dependencias:** Fase 4
**Estimación:** Eliminar 2 archivos

### Tarea 5.2 — Actualizar dependencias
Buscar y actualizar todos los imports que referencian `query.parser`, `query.executor`, `QueryEngine`, `QueryManager`.

**Criterio:** `grep -r "from query\|from query.parser\|from query.executor"` → 0 resultados.

**Dependencias:** Fase 5.1
**Estimación:** Variable según cantidad de imports

---

## Diagrama de dependencias

```
Fase 1 (CognitiveGraph core)
  └─► Fase 2 (Migrar índices: CKG, CPG, Delta, Trace)
        └─► Fase 3 (Tasks del walker)
        └─► Fase 4 (Reemplazar QueryManager)
              └─► Fase 5 (Limpieza: eliminar query/parser, executor)
```

## Total estimado: 7-9 módulos, ~700 líneas de código nuevo/ modificado

| Fase | Archivos | Líneas | Depende de |
|---|---|---|---|
| 1. CognitiveGraph core | 1 | ~280 | - |
| 2. Migrar índices | 4 | ~240 | Fase 1 |
| 3. Tasks del walker | 1 | ~60 | Fase 2 |
| 4. QueryManager wrapper | 1 | ~30 | Fase 2 |
| 5. Limpieza | 2+ | ~50 | Fase 4 |
| **Total** | **~9** | **~660** | |
