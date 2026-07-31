# Prompt Management — Módulo Centralizado

## 1. Situación Actual (dispersión)

```
📁 prompts/                          → markdown sueltos
📁 templates/                        → templates del spring
📁 _internal/templates/              → templates duplicados
📁 task-manifests/                   → metadata de tareas
📁 .cognitive/.../workflow.v3/prompts/ → prompts de nodos del workflow

📄 _walker/workflow_engine/application/orchestrator.py  → resolve_prompt()
📄 _walker/workflow_engine/context/composer.py          → ContextComposer
📄 _walker/presenter.py                                  → resuelve prompt IDs
📄 prompt_engine.py                                      → renderiza templates
📄 _internal/prompt_engine.py                            → duplicado
```

**Problemas:**
- Templates en 5+ ubicaciones
- Lógica de render en 4+ archivos
- Sin interfaz unificada
- Duplicación entre `prompt_engine.py` y `_internal/prompt_engine.py`
- ContextComposer mezcla recolección de datos con armado de prompt

## 2. Propuesta: Módulo `prompt/`

```
prompt/                               ← NUEVO: único responsable de prompts
├── __init__.py                       ← exporta PromptManager
├── manager.py                        ← PromptManager (facade)
├── loader.py                         ← carga templates de cualquier fuente
├── renderer.py                       ← renderiza templates con datos
├── collector.py                      ← recolecta datos (contexto, tareas, CKG)
└── templates/                        ← templates centralizados (.md, .txt)
```

### 2.1 PromptManager (facade)

```python
class PromptManager:
    """Único punto de entrada para todo lo relacionado con prompts.
    
    - Carga templates (de cualquier fuente)
    - Recolecta datos (contexto, tareas, CKG)
    - Renderiza templates con datos
    """
    
    def __init__(self, chain_dir: str):
        self._chain_dir = chain_dir
        self._loader = TemplateLoader()
        self._collector = DataCollector(chain_dir)
        self._renderer = TemplateRenderer()
    
    def resolve(self, prompt_id: str, context: dict | None = None) -> str:
        """Resuelve un prompt: carga template + recolecta datos + renderiza."""
        template = self._loader.load(prompt_id)
        data = self._collector.collect(context or {})
        return self._renderer.render(template, data)
    
    def render(self, template_name: str, data: dict) -> str:
        """Renderiza un template con datos (sin recolección automática)."""
        template = self._loader.load(template_name)
        return self._renderer.render(template, data)
```

### 2.2 TemplateLoader

```python
class TemplateLoader:
    """Carga templates de múltiples fuentes. Sin lógica de negocio."""
    
    def load(self, template_id: str) -> str:
        """Busca y carga un template por ID.
        
        Orden de búsqueda:
        1. prompt/templates/{id}.md (local)
        2. workflow.v3/prompts/{node}/{id}.prompt.json
        3. task-manifests/{id}.json
        4. templates/{id}.txt
        """
        for source in self._sources:
            path = source / f"{template_id}.md"
            if path.exists():
                return path.read_text()
            path = source / f"{template_id}.prompt.json"
                if path.exists():
                    return json.loads(path.read_text())["template"]
        raise TemplateNotFound(template_id)
```

### 2.3 DataCollector (hereda lógica de ContextComposer)

```python
class DataCollector:
    """Recolecta datos para inyectar en templates. Funciones puras.
    
    Reemplaza a ContextComposer. Cada provider registra su recolector.
    """
    
    def __init__(self, chain_dir: str):
        self._cg = CognitiveGraph(chain_dir)
        self._registry = TaskRegistry  # tareas disponibles
    
    def collect(self, context: dict) -> dict:
        """Recolecta datos según el contexto.
        
        Returns:
            Dict plano con datos para el template.
            Ej: {"activity": {...}, "tasks": [...], "knowledge": [...]}
        """
        return {
            "activity": self._get_activity(context.get("node_id")),
            "tasks": self._get_available_tasks(context.get("node_id")),
            "knowledge": self._get_relevant_knowledge(context),
            "project": self._get_project_info(),
            "state": self._get_current_state(),
        }
    
    def _get_available_tasks(self, node_id: str | None) -> list[dict]:
        """Tareas disponibles para el nodo actual, con sus input_schema."""
        registry, _ = bootstrap()
        tasks = registry.list_tasks_with_meta()
        return [{
            "name": t["name"],
            "description": t.get("description", ""),
            "input_schema": t.get("input_schema", {}),
            "usage": t.get("usage", ""),
        } for t in tasks]
    
    def _get_relevant_knowledge(self, context: dict) -> list[dict]:
        """Conocimiento relevante del CKG para el contexto actual."""
        # Nodos globales (scope=project) siempre se incluyen
        global_knowledge = self._cg.search("ckg", {"tag": "global"})
        # Nodos relevantes al tipo de tarea
        task_type = context.get("task_type", "")
        if task_type:
            relevant = self._cg.search("ckg", {"tag": task_type})
        return {
            "global": [h["_source"] for h in global_knowledge["hits"]["hits"]],
            "relevant": [h["_source"] for h in relevant["hits"]["hits"]] if task_type else [],
        }
```

### 2.4 TemplateRenderer

```python
class TemplateRenderer:
    """Renderiza templates con datos. Sin lógica de negocio.
    
    Usa formato simple {{variable}} o {% for %} {% endfor %}.
    Independiente del engine de templates.
    """
    
    def render(self, template: str, data: dict) -> str:
        """Reemplaza placeholders y ejecuta loops simples."""
        # {{variable}} → valor
        for k, v in data.items():
            template = template.replace(f"{{{{{k}}}}}", str(v))
        return template
```

## 3. Integración con el workflow

### 3.1 Antes (hoy)

```
orchestrator.resolve_prompt()
  → busca workflow.v3/prompts/{id}.prompt.json
  → ContextComposer.collect()
    → cada provider consulta QueryManager
    → arma ContextPieces
  → reemplaza placeholders
  → devuelve string
```

### 3.2 Después

```
orchestrator.resolve_prompt()
  → prompt_manager.resolve(prompt_id, context)
    → loader.load(prompt_id)          ← template
    → collector.collect(context)       ← datos
    → renderer.render(template, data)  ← prompt final
  → devuelve string
```

### 3.3 Migración

1. Crear módulo `prompt/` con PromptManager
2. Migrar templates de `templates/`, `_internal/templates/`, `prompts/` a `prompt/templates/`
3. `ContextComposer` pasa a ser `DataCollector` interno de `prompt/`
4. `orchestrator.resolve_prompt()` usa `prompt_manager.resolve()` en vez de lógica propia
5. Eliminar `prompt_engine.py` y `_internal/prompt_engine.py`
6. `resolve_prompt_id_to_message` en `presenter.py` usa `prompt_manager`

## 4. Árbol final

```
prompt/
├── __init__.py
├── manager.py              ← PromptManager (facade)
├── loader.py               ← TemplateLoader
├── renderer.py             ← TemplateRenderer
├── collector.py            ← DataCollector (ex ContextComposer)
└── templates/              ← templates centralizados
    ├── activity.md         ← prompt de actividad (ex workflow.v3/prompts/)
    ├── task.md             ← prompt de tarea (ex task-manifests/)
    └── knowledge.md        ← conocimiento global inyectado (ex CKG)

_walker/workflow_engine/
└── context/
    └── composer.py         ← DEPRECATED (reemplazado por prompt/collector.py)
```

**Impacto:** 5 ubicaciones de templates → 1. 4 archivos de lógica → 1 módulo. Sin duplicación.
