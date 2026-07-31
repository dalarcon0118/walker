# Unified Query Language — CPG · CKG · DeltaGraph · Trace · State

> **Estado:** Diseño  
> **Versión:** 1.0  
> **Dependencias:** walker3, TraceLogger, DeltaGraph indexer  

---

## 1. El problema

Hoy cada capa de datos tiene su propia forma de consulta:

| Capa | Consulta actual | Problema |
|------|----------------|----------|
| CPG | `cpg-grep "function:submit"` | Sintaxis propia |
| CKG | `ckg-query "type:decision"` | Sintaxis propia |
| DeltaGraph | `walker3 query/delta --query "texto"` | Sintaxis propia |
| Trace | ❌ No existe | No hay consultas |
| State | `walker3 status` | Solo lectura, no filtros |

El agente debe aprender 4 sintaxis distintas. Y no hay un modelo unificado de **interacciones** (tools, mensajes, archivos) que permita responder preguntas como:

- *"¿Qué tools usó el agente en TASK-012?"*
- *"¿Qué archivos modificó durante la última hora?"*
- *"¿Qué mensajes del usuario recibió mientras trabajaba en events.py?"*

---

## 2. Modelo de datos: TraceEntry

Cada interacción se modela como un objeto estructurado:

```python
@dataclass
class TraceEntry:
    event_type: str       # tool_call | file_change | user_msg | llm_resp
                          # | transition | submit | error
    timestamp: str
    session_id: str
    task_id: str
    tool: str             # Read | Write | Edit | Bash | Grep | Question
                          # | walker3 | pytest | qlty | git ...
    input: dict           # Parametros de la herramienta
    output: dict          # Resultado (exit_code, data, error)
    files: list[str]      # Archivos involucrados
    message: str          # Texto del mensaje (Question/LLM)
    metadata: dict        # Contexto: node, sub_state, sprint, project
```

Ejemplo en JSONL:

```json
{"event_type":"tool_call","timestamp":"2026-07-25T10:00:00Z","task_id":"TASK-012","tool":"Write","input":{"file_path":"events.py"},"files":["events.py"],"output":{"exit_code":0}}
{"event_type":"user_msg","timestamp":"2026-07-25T10:01:00Z","task_id":"TASK-012","message":"agrega el manejador de eventos faltante"}
{"event_type":"file_change","timestamp":"2026-07-25T10:02:00Z","task_id":"TASK-012","files":["handler.py"],"tool":"Edit","output":{"exit_code":0}}
{"event_type":"tool_call","timestamp":"2026-07-25T10:05:00Z","task_id":"TASK-012","tool":"Read","input":{"file_path":"events.py"}}
```

---

## 3. Lenguaje de consulta unificado (Elasticsearch DSL adaptado)

### 3.1 Sintaxis CLI (simple)

```
walker3 query/<layer> "<campo>:<valor> [<campo>:<valor> ...]"
walker3 query/<layer> '<JSON query>'
```

### 3.2 Operadores

| CLI | Elasticsearch | Descripción |
|-----|---------------|-------------|
| `task:TASK-012` | `{"term":{"task_id":"TASK-012"}}` | Match exacto |
| `tool:Write` | `{"term":{"tool":"Write"}}` | Match exacto |
| `type:user_msg` | `{"term":{"event_type":"user_msg"}}` | Match exacto |
| `file:events.py` | `{"term":{"files":"events.py"}}` | Match en array |
| `time:24h` | `{"range":{"timestamp":{"gte":"now-24h"}}}` | Rango temporal |
| `time:7d` | `{"range":{"timestamp":{"gte":"now-7d"}}}` | Rango temporal |
| `limit:10` | `{"size":10}` | Máx resultados |
| `class:EventDispatcher` | `{"match":{"label":"EventDispatcher"}}` | Match texto |
| `function:submit` | `{"match":{"label":"submit"}}` | Match texto |
| `calls:execute` | `{"match":{"calls":"execute"}}` | Match texto |
| `scope:engine` | `{"term":{"scope":"engine"}}` | Match exacto |
| `node:VERIFY` | `{"term":{"node":"VERIFY"}}` | Match exacto |

### 3.3 Multi-filtro (AND implícito)

```
walker3 query/trace "task:TASK-012 tool:Write time:24h"

# Compila internamente a:
POST /trace/_search
{
  "query": {
    "bool": {
      "must": [
        {"term": {"task_id": "TASK-012"}},
        {"term": {"tool": "Write"}}
      ],
      "filter": [
        {"range": {"timestamp": {"gte": "now-24h"}}}
      ]
    }
  }
}
```

### 3.4 JSON query (complejo)

```
walker3 query/trace --query '{
  "bool": {
    "must": [
      {"term": {"task_id": "TASK-012"}},
      {"terms": {"tool": ["Write", "Edit"]}}
    ],
    "must_not": [
      {"term": {"files": ".gitignore"}}
    ],
    "filter": [
      {"range": {"timestamp": {"gte": "now-24h"}}}
    ]
  },
  "size": 20,
  "sort": [{"timestamp": {"order": "desc"}}]
}'
```

---

## 4. Capas queryables

Todas las capas usan el mismo motor de consultas. Cambia el índice y los campos disponibles:

| Capa | Índice | Campos clave | Ejemplo |
|------|--------|-------------|---------|
| **CPG** | `cpg.json.gz` | `class`, `function`, `file`, `calls`, `imports`, `bug`, `security` | `query/cpg "class:EventDispatcher"` |
| **CKG** | `ckg.json.gz` | `type`, `scope`, `label`, `description`, `task_id` | `query/ckg "type:decision scope:event"` |
| **DeltaGraph** | `delta-index.json.gz` | `task_id`, `file`, `objective`, `predicate`, `risk`, `domain` | `query/delta "task:event system"` |
| **Trace** | `trace-index.json.gz` | `task_id`, `tool`, `event_type`, `file`, `timestamp`, `session_id` | `query/trace "task:TASK-012 tool:Write"` |
| **State** | `sprint-state.json` | `node`, `sub_state`, `task_id`, `sprint_id`, `status` | `query/state "node:VERIFY status:active"` |

---

## 5. Resultados unificados

```json
{
  "layer": "trace",
  "query": {"bool": {"must": [{"term": {"task_id": "TASK-012"}}]}},
  "total": 42,
  "took_ms": 12,
  "results": [
    {
      "type": "tool_call",
      "timestamp": "2026-07-25T10:00:00Z",
      "tool": "Write",
      "files": ["events.py"],
      "snippet": "Write events.py (exit: 0)"
    },
    {
      "type": "user_msg",
      "timestamp": "2026-07-25T10:01:00Z",
      "message": "agrega el manejador de eventos faltante",
      "snippet": "Usuario: agrega el manejador de eventos faltante"
    }
  ],
  "aggregations": {
    "by_tool": {"Read": 15, "Write": 10, "Bash": 8, "Edit": 5, "Question": 4},
    "by_file": {"events.py": 8, "handler.py": 5}
  }
}
```

---

## 6. Indexación del Trace

```
Hook post-tool (cada tool call)
       │
       └── llama a: walker3 trace/log --entry '{"event_type":"tool_call",...}'
            │
            └── escribe en: trace.log (append-only JSONL en chain_dir)
                 │
                 └── TraceIndexer.index_task(task_id)
                      │                      ← se ejecuta después de PO_ACCEPT
                      └── escribe en: .indices/trace-index.json.gz
                           │
                           └── walker3 query/trace --query "..."
```

### 6.1 Integración con hooks

```bash
# delta-post-tool-use.sh
# Después de CADA tool call exitosa, registrar en trace.log
walker3 trace/log --entry "$(cat <<-EOF
{
  "event_type": "tool_call",
  "tool": "$TOOL",
  "input": $(echo "$TOOL_INPUT" | jq -c 'del(.content)'),
  "files": $(extract_files "$TOOL" "$TOOL_INPUT"),
  "task_id": "${WALKER_TASK_ID}",
  "session_id": "${WALKER_SESSION_ID}"
}
EOF
)"
```

### 6.2 TraceIndexer

```python
class TraceIndexer:
    """Indexa trace.log en el inverted index de DeltaGraph.

    Indexa tool calls, mensajes y archivos.
    Permite queries tipo: "task:X tool:Y" o "task:X file:Z".
    """

    def index_task(self, chain_dir: str, task_id: str) -> int:
        """Indexa entries del trace.log para una tarea específica."""
        entries = self._load_trace(chain_dir, task_id)
        index = self._load_index(chain_dir)
        for entry in entries:
            for token in self._tokenize(entry):
                index.add(token, entry)
        self._save_index(chain_dir, index)
```

### 6.3 Integración en workflow.v3

```json
// verify.node.json → PO_ACCEPT.side_effects
{
  "type": "index_trace",
  "params": {},
  "description": "Indexar interacciones de la tarea en TraceLog"
}

// sprint.node.json → CLOSE.side_effects
{
  "type": "reindex_all",
  "params": {},
  "description": "Reindexar Trace + DeltaGraph al cerrar sprint"
}
```

---

## 7. Agregaciones (analytics)

Además de buscar entradas, el motor soporta agregaciones tipo Elasticsearch:

```bash
# Tools más usadas en TASK-012
walker3 query/trace "task:TASK-012" --aggs by_tool

# Archivos más modificados
walker3 query/trace "task:TASK-012" --aggs by_file

# Distribución de tools en el tiempo
walker3 query/trace "task:TASK-012" --aggs by_hour

# Combinado: qué tools y qué archivos
walker3 query/trace "task:TASK-012" --aggs by_tool,by_file
```

Resultado de agregaciones:

```json
{
  "aggregations": {
    "by_tool": {
      "buckets": [
        {"key": "Read", "doc_count": 15},
        {"key": "Write", "doc_count": 10},
        {"key": "Bash", "doc_count": 8}
      ]
    },
    "by_file": {
      "buckets": [
        {"key": "events.py", "doc_count": 8, "tools": ["Write", "Read"]},
        {"key": "handler.py", "doc_count": 5, "tools": ["Edit", "Read"]}
      ]
    }
  }
}
```

---

## 8. ¿Qué responde esto?

| Pregunta | Consulta |
|----------|----------|
| ¿Qué tools usó el agente en TASK-012? | `query/trace "task:TASK-012" --aggs by_tool` |
| ¿Qué archivos modificó en la última hora? | `query/trace "type:file_change time:1h" --aggs by_file` |
| ¿Qué mensajes recibió mientras trabajaba en events.py? | `query/trace "file:events.py type:user_msg"` |
| ¿Cuántas veces leyó el agente el archivo X? | `query/trace "task:TASK-012 file:X tool:Read" --aggs by_file` |
| ¿Qué tareas tocaron el archivo Y? | `query/trace "file:Y" --aggs by_task` |
| ¿Cuánto tiempo tomó la tarea TASK-012? | `query/trace "task:TASK-012 type:tool_call" --aggs by_hour` |
| ¿Qué decisiones de diseño hay sobre el módulo X? | `query/ckg "scope:X type:decision"` |
| ¿Hay bugs en la función submit? | `query/cpg "function:submit bug:all"` |
| ¿Qué tareas se relacionan con events? | `query/delta "event system"` |
| ¿En qué nodo del workflow estamos? | `query/state "node:VERIFY"` |

---

## 9. Implementación

### Archivos nuevos

| Archivo | Propósito |
|---------|-----------|
| `walker3 trace/log --entry <json>` | Escribe entrada en trace.log |
| `walker3 query/trace --query <q>` | Consulta índice de trace |
| `walker3 query/<layer> --query <q>` | Consulta unificada (capa: cpg,ckg,delta,trace,state) |
| `trace_indexer.py` | Indexa trace.log en inverted index |
| `unified_query.py` | Motor de consultas: parsea CLI syntax → ES-like JSON → índices |

### Cambios en existentes

| Archivo | Cambio |
|---------|--------|
| `workflow.v3/nodes/verify.node.json` | Agregar side-effect `index_trace` en PO_ACCEPT |
| `workflow.v3/nodes/sprint.node.json` | Agregar side-effect `reindex_all` en CLOSE |
| `hooks/delta-post-tool-use.sh` | Escribir trace entry después de cada tool |
| `deltagraph/indexer.py` | Agregar TraceIndexer |
| `deltagraph/searcher.py` | Unificar con UnifiedQueryEngine |
