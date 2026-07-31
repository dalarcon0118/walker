# CognitiveGraph — Diseño Formal

## 1. Propósito

Capa unificada de infraestructura para las 4 memorias del walker (CPG, CKG, DeltaGraph, TraceGraph). Como un ORM/ODM, pero para grafos de conocimiento. Los dominios definen sus índices sobre CognitiveGraph; el agente consulta cualquier índice con la misma API.

## 2. Estructura

```
_walker/shared/cognitive_graph.py    ← infraestructura base
├── CognitiveGraph.register_index()  ← crear índice con schema + storage
├── .search(index, query)            ← consultar
├── .search_many({index: query})     ← multi-índice
├── .store(index, id, data)          ← indexar/actualizar
├── .get(index, id)                  ← obtener por ID
├── .delete(index, id)               ← eliminar
├── .schema(index)                   ← mapping del índice
├── .metrics(index)                  ← stats del índice
└── .list_indexes()                  ← índices disponibles

Cada dominio extiende o configura CognitiveGraph para su índice:
  ckg/  → register_index("ckg", schema={...})
  cpg/  → register_index("cpg", schema={...})
  delta/ → register_index("delta", schema={...})
  trace/ → register_index("trace", schema={...})
```

## 3. Schema de un índice

```python
CognitiveGraph.register_index("ckg", {
    # Mapeo de campos: query_field → document_field
    "fields": {
        "type": "type",
        "scope": "scope",
        "task": "metadata.task_id",
        "label": "statement",
    },
    # Tipos de documentos en este índice
    "types": {
        "decision": {"description": "Decisión de arquitectura"},
        "refactor": {"description": "Refactorización aplicada"},
        "convention": {"description": "Convención de estilo"},
        "pattern": {"description": "Patrón de diseño"},
    },
    # Storage: dónde persiste
    "storage": {
        "path": "data/ckg.json.gz",
        "format": "json.gz",
    },
    # Descripción para el agente
    "description": "Knowledge Graph: decisiones, convenciones, patrones",
})
```

## 4. API completa

### 4.1 Consultas

```python
cg = CognitiveGraph(chain_dir)

# Un solo índice (como GET /ckg/_search)
cg.search("ckg", {"type": "refactor"})
cg.search("cpg", {"function": "cmd_create"})

# Múltiples índices (como GET /ckg,cpg/_search)
cg.search_many({
    "ckg": {"type": "refactor"},
    "cpg": {"function": "cmd_create"},
    "delta": {"status": "active"},
})

# Schema (como GET /ckg/_mapping)
cg.schema("ckg")
# → {
#     "fields": {"type": "type", "scope": "scope", ...},
#     "types": {"decision": 53, "refactor": 11, ...},
#     "total_docs": 196,
# }

# Stats (como GET /ckg/_stats)
cg.metrics("ckg")
# → {
#     "total_docs": 196,
#     "total_edges": 81,
#     "storage_size": "12KB",
#     "last_updated": "2026-07-27T..."
# }
```

### 4.2 Escritura

```python
# Indexar documento (como POST /ckg/_doc)
cg.store("ckg", "decision-001", {
    "type": "decision",
    "subject": "ast-grep vs sg outline",
    "statement": "CPG se construye con ast-grep...",
    "rationale": "sg outline solo daba outline superficial...",
})

# Obtener por ID (como GET /ckg/_doc/id)
cg.get("ckg", "decision-001")

# Eliminar (como DELETE /ckg/_doc/id)
cg.delete("ckg", "decision-001")
```

### 4.3 Sintaxis de query

Dos formatos, igual que hoy:

```python
# Formato simple: {campo: valor}
cg.search("ckg", {"type": "refactor"})
cg.search("cpg", {"file": "events.py", "type": "function"})

# Formato JSON DSL (como Elasticsearch)
cg.search("ckg", {
    "bool": {"must": [
        {"term": {"type": "refactor"}},
        {"term": {"scope": "engine"}},
    ]}
})
```

## 5. Respuesta unificada (envelope)

Toda consulta devuelve el mismo formato:

```python
{
    "took": 5,                          # ms
    "index": "ckg",
    "hits": {
        "total": 11,
        "hits": [
            {
                "_id": "refactor-001",
                "_index": "ckg",
                "_score": 1.0,
                "_source": {
                    "type": "refactor",
                    "subject": "cmd_create",
                    "statement": "Extraer from-analysis de cmd_create",
                    "rationale": "cmd_create tenia 447 lineas...",
                    "location": "spring/create.py",
                    "api": "cmd_create(args)",
                    "metadata": {"cyclo_before": 67, "cyclo_after": 47},
                }
            }
        ]
    },
    "schema": {                         # mapping del índice
        "fields": {"type": "type", "scope": "scope", ...},
        "types": {"refactor": 11, "decision": 53, ...},
    }
}
```

Multi-índice:

```python
cg.search_many({"ckg": {"type": "refactor"}, "cpg": {"function": "cmd_create"}})
→ {
    "took": 8,
    "responses": {
        "ckg": { "hits": {...} },
        "cpg": { "hits": {...} }
    }
}
```

## 6. Uso desde el Task Registry

Los tasks registrados en el walker usan CognitiveGraph directamente:

```python
# tasks/kg.py — Consultas del agente
from _walker.shared.cognitive_graph import CognitiveGraph

def kg_search(ctx, data):
    cg = CognitiveGraph(ctx.chain_dir)
    query = data.get("query", "")
    index = data.get("index", "ckg")
    # search auto-detecta si es query simple o JSON DSL
    result = cg.search(index, {"statement": query})
    return TaskResult.success(data=result)

def kg_store(ctx, data):
    cg = CognitiveGraph(ctx.chain_dir)
    index = data.get("index", "ckg")
    doc_id = data.get("id")
    doc = {
        "type": data.get("type", "decision"),
        "subject": data.get("subject", ""),
        "statement": data.get("statement", ""),
        "rationale": data.get("rationale", ""),
        "metadata": {"task_id": ctx.task_id},
    }
    cg.store(index, doc_id, doc)
    return TaskResult.success(data={"ok": True, "id": doc_id})

def register(registry):
    registry.register("kg_search", kg_search)
    registry.register("kg_store", kg_store)
    registry.register("kg_schema", lambda ctx, d: TaskResult.success(
        data=CognitiveGraph(ctx.chain_dir).schema(d.get("index", "ckg"))
    ))
```

```python
# Uso desde CLI:
walker3 delta-skill/task/kg_search --index ckg --query refactor
walker3 delta-skill/task/kg_search --index cpg --query cmd_create
walker3 delta-skill/task/kg_store --index ckg --type refactor --statement "..."
walker3 delta-skill/task/kg_schema --index ckg
```

## 7. Migración desde el QueryManager actual

El `QueryManager` actual y `QueryEngine` se reemplazan por `CognitiveGraph`. No se rompe nada porque:

1. `QueryManager` se convierte en un wrapper que delega en `CognitiveGraph`
2. `CognitiveGraph.search_many()` recibe el mismo formato que `QueryManager.query()`
3. Los providers (CKGProvider, CPGProvider) se actualizan para usar `CognitiveGraph`

```python
# Antes:
qm = QueryManager(chain_dir)
qm.query({"cpg": {"file": "events.py"}, "ckg": {"type": "decision"}})

# Después:
cg = CognitiveGraph(chain_dir)
cg.search_many({"cpg": {"file": "events.py"}, "ckg": {"type": "decision"}})
```

## 8. Resumen

| Aspecto | CognitiveGraph |
|---|---|
| Es | Infraestructura tipo ODM para memorias del walker |
| No es | Un motor de base de datos ni un reemplazo de Elasticsearch |
| Índices | ckg, cpg, delta, trace (cada uno con schema propio) |
| Queries | Simple `{campo:valor}` o JSON DSL |
| Respuesta | Envelope unificado con `hits`, `schema`, `took` |
| Escritura | `store`, `get`, `delete` por índice |
| Multi-índice | `search_many()` como ES multi-search |
| Tasks | `kg_search`, `kg_store`, `kg_schema` en el task registry |
