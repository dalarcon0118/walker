# Protocolo de 6 Capas — Contexto Profundo por Tarea

## Resumen

El protocolo de 6 capas fuerza al LLM a investigar el estado real del código **antes de escribir cualquier línea**, evitando:
- Crear archivos/clases que ya existen
- Duplicar funcionalidad
- Empezar a implementar sin entender el contexto

## Implementación

### 1. Schema (`plan.schema.json`)

Campo `task_context` agregado como **requerido** en cada tarea:

```json
"task_context": {
  "type": "object",
  "required": ["observation", "structure", "behavior", "causality", "purpose", "principles"],
  "properties": {
    "observation": { "type": "string", "minLength": 10 },
    "structure": { "type": "string", "minLength": 10 },
    "behavior": { "type": "string", "minLength": 10 },
    "causality": { "type": "string", "minLength": 10 },
    "purpose": { "type": "string", "minLength": 10 },
    "principles": { "type": "string", "minLength": 10 }
  }
}
```

### 2. Validación (`task.py advance`)

Antes de avanzar una tarea, el script verifica:
- ✅ `task_context` existe
- ✅ Las 6 capas tienen contenido (mínimo 10 caracteres cada una)
- ❌ Si falta alguna capa → **ENFORCEMENT BLOCKED** → no se puede avanzar

### 3. Display (CONTEXT DISCOVERY)

Al ejecutar `task.py advance <chain> <task>`, el output incluye:

```
======================================================================
🔍 CONTEXT DISCOVERY — estado real del código + contexto profundo
======================================================================

  📂 ARCHIVOS AFECTADOS:
  ✅ EXISTE: features/agent/decisions/domain/decision.py
     Primeras líneas:
       """Domain VOs for the decisions feature..."""
  🆕 NUEVO: features/agent/decisions/domain/new_module.py

  🔌 APIS / SÍMBOLOS A CREAR:
  ⚠️  YA EXISTE: CpgStateTracker
     projects/.../state_tracker.py:class CpgStateTracker
  🆕 NUEVO: CpgStateChanged

  🧠 CONTEXTO PROFUNDO (protocolo 6 capas):
  1. Observación: ¿Qué existe?
       Existe decision.py, decision_schemas.py, schema.py...
  2. Estructura: ¿Cómo está organizado?
       features/agent/decisions/domain/decision.py (VOs)...
  3. Comportamiento: ¿Cómo interactúa?
       register_decision.py registra hechos cognitivos...
  4. Causalidad: ¿Por qué ocurre?
       Necesita eliminar is_vague_choice()...
  5. Finalidad: ¿Para qué existe?
       Arquitectura limpia: dominio puro sin I/O...
  6. Principios: ¿Qué leyes generales gobiernan?
       DDD: dominio puro sin dependencias externas...

  ⚠️  ANTES DE ESCRIBIR CÓDIGO: lee el contexto real arriba.
  Si algo YA EXISTE, ajusta la implementación (no lo dupliques).
======================================================================
```

### 4. SKILL.md

Actualizado con:
- **PASO 2.5**: Descubrir contexto real (OBLIGATORIO)
- **Protocolo de 6 capas**: documentado como requerimiento
- **PASO 5**: ahora incluye paso 0 de leer context discovery

## Uso

### Para el LLM (creación de sprint)

Al crear un sprint con `spring.py create`, el LLM DEBE generar `task_context` para cada tarea:

```python
task_context = {
    "observation": "Hechos observados del estado actual...",
    "structure": "Componentes, dependencias, interfaces...",
    "behavior": "Flujos, estados, secuencias, contratos...",
    "causality": "Restricciones, invariantes, causas raíz...",
    "purpose": "Objetivos, criterios de éxito, trade-offs...",
    "principles": "Patrones reutilizables, heurísticas..."
}
```

### Para el LLM (ejecución de tarea)

```bash
python3 scripts/task.py advance <chain_dir> <task_id>
```

El output muestra:
1. **ARCHIVOS AFECTADOS**: ¿existen? preview si existen
2. **APIS/SÍMBOLOS**: ¿ya existen?
3. **CONTEXTO PROFUNDO**: las 6 capas
4. **ENFORCEMENT**: valida task_context completo
5. **TASK LOOP**: verificaciones

Solo después de leer todo esto, el LLM debe escribir código.

## Beneficios

1. **No duplicación**: el LLM ve que un archivo/clase ya existe
2. **Contexto profundo**: entiende el "por qué" antes del "cómo"
3. **Enforcement**: no puede avanzar sin investigación
4. **Trail audit**: cada tarea queda con su contexto documentado
