# ADR-WALKER-056: Migración del engine — `core/engine/` → `shared/core/engine/` + `data/workflow/`

> **Status:** Ejecutado
> **Fecha:** 2026-08-25
> **Autor:** Walker (agente) + PO
> **Complementa:** ADR-WALKER-054 (almacenamiento cognitivo unificado), ADR-WALKER-053 (ProjectContext), ADR-WALKER-045/046 (composition root único)

---

## 1. Problema

### 1.1 Antipatrones en el engine

| # | Antipatrón | Evidencia | Impacto |
|---|---|---|---|
| 1 | **`bootstrap.py` = Service Locator global** | `get_module_service("cognitive_graph")` en 19+ archivos de features — acceden a servicios vía string key en un diccionario global | Acoplamiento implícito: renombrar un servicio rompe callers en runtime sin warning en compile-time |
| 2 | **`mcp.py` = Protocol coupling** | `shared/core/engine/mcp.py` importa `McpServer` de mcp-sdk y registra tools directamente | El engine (domain core) depende de MCP (protocolo de transporte) — viola arquitectura hexagonal |
| 3 | **Directorio mal ubicado** | `core/engine/` vive en la raíz del proyecto junto a `features/` y `libs/` | No hay separación clara entre core genérico y features de negocio |
| 4 | **Workflow JSON hardcodeado** | `core/engine/workflow/workflow.json` en el directorio del engine | Los JSON de definición de workflow están mezclados con código Python del engine |

### 1.2 Por qué importa ahora

El engine es el corazón del sistema — workflow, orchestrator, context registry, task dispatcher. Si el core depende de MCP y features dependen del core vía service locator, cualquier cambio estructural crea un efecto dominó de 187+ archivos.

---

## 2. Decisión

**Migrar `core/engine/` → `shared/core/engine/` y `core/engine/workflow/` → `data/workflow/`, eliminando la ubicación inconsistente y preparando la eliminación de antipatrones.**

### D1 — Mover workflow JSON a `data/workflow/`

- `core/engine/workflow/` (archivos JSON) → `data/workflow/`
- `get_engine_workflow_dir()` en `path_resolver/__init__.py` retorna `data/workflow`
- `get_engine_workflow_path()` retorna `data/workflow/workflow.json`
- Los JSON de definición de workflow quedan separados del código Python del engine

### D2 — Mover engine Python a `shared/core/engine/`

- Todos los `.py` de `core/engine/` → `shared/core/engine/`
- Subdirectorios: `application/`, `context/`, `domain/`, `infrastructure/`, `interface/`, `legacy/`
- `bootstrap.py` y `mcp.py` se mueven también (no se eliminan en esta fase)

### D3 — Actualizar ~187 imports

- `from core.engine.*` → `from shared.core.engine.*`
- `core.engine.workflow.*` → `data.workflow.*`
- Comentarios y docstrings actualizados

### D4 — Eliminar directorio viejo

- `core/engine/` eliminado completamente
- `core/__init__.py` actualizado (solo queda el paquete vacío)

### D5 — Fix de package discovery

- `bootstrap_providers()` hardcodeaba `package_name="core.engine.context"` → corregido a `"shared.core.engine.context"`
- Sin este fix, `importlib.util.find_spec("core.engine.context")` fallaba con `ModuleNotFoundError`

---

## 3. Alternativas evaluadas

| Alternativa | Pros | Contras | Decisión |
|---|---|---|---|
| **A. Dejar en `core/engine/`** | Zero risk | Mantiene antipatrones + ubicación inconsistente | Rechazada |
| **B. Mover a `shared/core/engine/` (elegida)** | Prepara eliminación de service locator; ubicación coherente con otros shared modules | 187 imports + risk de romper runtime | Aceptada |
| **C. Refactor completo (DI + adapter)** | Elimina antipatrones de raíz | Alto riesgo, muchas dependencias, requiere composition roots | Diferida |

---

## 4. Failure Scenarios

| # | Fallo | Causa raíz | Mitigación | Detección |
|---|---|---|---|---|
| 1 | `ModuleNotFoundError: No module named 'core.engine'` en runtime | `bootstrap_providers()` hardcodeaba `package_name="core.engine.context"` — el discovery de providers fallaba silenciosamente | Fix inmediato del package_name (D5) | Test de smoke que valida `AppContainer.build()` + `_get_tool_catalog()` returns 31 tools |
| 2 | ~187 archivos con imports rotos | Rename masivo de `core.engine` → `shared.core.engine` | Script de reescritura + grep de verificación (0 imports viejos restantes) | `grep -rn "core\.engine" --include="*.py"` retorna solo `shared/core/engine` paths |
| 3 | Workflow JSON no encontrado | `get_engine_workflow_dir()` retornaba directorio viejo | Actualización de `path_resolver/__init__.py` para apuntar a `data/workflow` | `get_engine_workflow_path().exists() == True` |

---

## 5. Consecuencias

### 5.1 Positivas
- Directorio del engine coherente con la arquitectura (`shared/core/` = código genérico reutilizable)
- Workflow JSON separado de código Python (facilita edits sin risk de imports)
- Preparación para eliminar `bootstrap.py` (service locator) y `mcp.py` (protocol coupling)

### 5.2 Negativas
- 187 archivos modificados (merge conflicts potenciales en branches paralelas)
- `bootstrap.py` y `mcp.py` siguen como antipatrones (eliminación diferida)

### 5.3 Déuda técnica pendiente
- **Eliminar `bootstrap.py`**: reemplazar `get_module_service()` por composition roots + DI explícita
- **Eliminar `mcp.py`**: extraer a un adapter en `features/main/cli_interface/`
- **Renombrar `KgExtractInput`** y otros dataclasses con prefijo `kg_`

---

## 6. Verification

| Check | Resultado |
|---|---|
| `_get_tool_catalog()` retorna 31 tools | ✅ |
| `get_engine_workflow_path().exists()` | ✅ True |
| `from shared.core.engine.engine import load_workflow` | ✅ OK |
| `grep -rn "core\.engine" --include="*.py"` (sin shared/) | ✅ 0 resultados |
| `core/engine/` eliminado | ✅ Solo `__init__.py` + `__pycache__` en `core/` |
