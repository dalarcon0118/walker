# Task-Driven Prompt Composition — Diseño Formal

## 1. Problema

Hoy los nodos del workflow definen:
- El prompt de la actividad
- El esquema de datos que el LLM debe enviar
- Las transiciones disponibles

Esto acopla el nodo a las tareas específicas. Si cambia una tarea, hay que cambiar el nodo. Si agregas una tarea nueva, hay que modificar el nodo para que la incluya.

## 2. Solución

Inversión de control: el nodo solo describe la actividad. Las tareas registran su propio contrato de datos (`input_schema`). El prompt se compone dinámicamente uniendo:

```
prompt = actividad (nodo) + contratos de tareas disponibles (task registry)
```

## 3. Componentes

### 3.1 Nodo → solo describe la actividad

```json
// workflow.v3/nodes/ANALYZE/node.json
{
  "id": "ANALYZE",
  "activity": "Analiza el problema y reúne evidencia antes de implementar.",
  "description": "Examina el código existente, identifica patrones, detecta riesgos.",
  "available_tasks": ["*"],  // todas las tareas registradas están disponibles
  "gates": [
    {"name": "analysis_approved", "target": "IMPLEMENT", "description": "Análisis completado, proceder a implementar"},
    {"name": "needs_refine", "target": "REFINE", "description": "El análisis necesita refinamiento"}
  ]
}
```

Opcionalmente, el nodo puede restringir qué tareas están disponibles:

```json
{
  "id": "TASK_SPEC",
  "activity": "Especifica los detalles de una tarea del sprint.",
  "available_tasks": ["spec_task", "validate_plan"],
  "gates": [...]
}
```

### 3.2 Tarea → registra su contrato de datos

```json
// task-manifests/spec_task.json
{
  "name": "spec_task",
  "description": "Completa los campos faltantes de una tarea según el schema.",
  "input_schema": {
    "required": ["task_id", "title", "type", "domain", "acceptance_criteria"],
    "optional": ["description", "depends_on", "risks"],
    "example": {
      "task_id": "TASK-001",
      "title": "Implementar autenticación JWT",
      "type": "feature",
      "domain": "security",
      "acceptance_criteria": ["Usuario puede iniciar sesión", "Token expira en 24h"]
    }
  },
  "output_schema": {
    "ok": "boolean",
    "data": {"task_id": "string", "saved": "boolean"}
  },
  "usage": "walker3 <project>/task/spec_task --task_id TASK-001 --title ..."
}
```

```json
// task-manifests/analyze.json
{
  "name": "analyze",
  "description": "Ejecuta análisis profundo del código.",
  "input_schema": {
    "required": ["chain_dir", "files"],
    "optional": ["depth"],
    "example": {"chain_dir": "/path/to/sprint", "files": ["src/main.py"]}
  },
  "usage": "walker3 <project>/task/analyze --files ..."
}
```

### 3.3 Task Registry → expone contratos

```python
# El LLM descubre qué tareas están disponibles y qué necesitan:
registry.list_tasks_with_meta()
→ [
    {"name": "spec_task", "description": "...", "input_schema": {...}},
    {"name": "analyze", "description": "...", "input_schema": {...}},
  ]
```

### 3.4 Compositor → arma el prompt dinámicamente

```python
def compose_prompt(node_id, available_tasks):
    node = load_node(node_id)
    tasks = filter_tasks(available_tasks, node.get("available_tasks", ["*"]))
    
    parts = [
        f"=== ACTIVIDAD: {node['id']} ===",
        node["activity"],
        node.get("description", ""),
        "",
        "=== TAREAS DISPONIBLES ===",
    ]
    
    for task in tasks:
        manifest = load_task_manifest(task)
        parts.append(f"\n📋 {manifest['name']}")
        parts.append(f"   {manifest['description']}")
        parts.append(f"   Para ejecutar, envía:")
        for field in manifest["input_schema"]["required"]:
            parts.append(f"     - {field} (requerido)")
        for field in manifest["input_schema"].get("optional", []):
            parts.append(f"     - {field} (opcional)")
        parts.append(f"   Ejemplo: {json.dumps(manifest['input_schema']['example'])}")
    
    return "\n".join(parts)
```

## 4. Prompt resultante

```
=== ACTIVIDAD: ANALYZE ===
Analiza el problema y reúne evidencia antes de implementar.
Examina el código existente, identifica patrones, detecta riesgos.

=== TAREAS DISPONIBLES ===

📋 spec_task
   Completa los campos faltantes de una tarea según el schema.
   Para ejecutar, envía:
     - task_id (requerido)
     - title (requerido)
     - type (requerido)
     - domain (requerido)
     - acceptance_criteria (requerido)
     - description (opcional)
     - depends_on (opcional)
   Ejemplo: {"task_id": "TASK-001", "title": "Implementar JWT", ...}

📋 analyze
   Ejecuta análisis profundo del código.
   Para ejecutar, envía:
     - chain_dir (requerido)
     - files (requerido)
     - depth (opcional)
   Ejemplo: {"chain_dir": "/path/to/sprint", "files": ["src/main.py"]}

=== TRANSICIONES DISPONIBLES ===
→ analysis_approved: Ir a IMPLEMENT
→ needs_refine: Ir a REFINE
```

## 5. El LLM decide

El LLM analiza la actividad, mira las tareas disponibles con sus contratos de datos, y decide:

```python
# 1. "Necesito analizar el código primero"
llm.call("analyze", {"chain_dir": "...", "files": ["src/cpg/__init__.py"]})

# 2. "Ahora tengo contexto, voy a especificar la tarea"
llm.call("spec_task", {"task_id": "TASK-001", "title": "Implementar X", ...})

# 3. "Análisis completo, transiciono"
llm.call("transition", {"gate": "analysis_approved"})
```

## 6. Beneficios

| Aspecto | Hoy | Propuesta |
|---|---|---|
| Acoplamiento nodo-tarea | Alto (nodo define esquema) | Bajo (tarea define su propio contrato) |
| Agregar tarea nueva | Modificar nodo | Solo registrar tarea |
| Descubrimiento | LLM sabe por prompt fijo | LLM descubre dinámicamente |
| Contrato de datos | En el prompt del nodo | En el task manifest (único lugar) |
| Validación | En el código de la tarea | En el schema del manifest |

## 7. Migración

1. Agregar `input_schema` a los task-manifests existentes (empezar con 3-5 tareas)
2. Modificar el compositor para inyectar contratos en el prompt
3. Simplificar los prompts de nodo (quitar esquemas, dejar solo actividad)
4. Los nodos dejan de definir qué tareas existen — las tareas se auto-registran
