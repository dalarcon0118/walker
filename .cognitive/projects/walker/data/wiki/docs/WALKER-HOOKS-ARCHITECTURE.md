# Walker3 + Hooks — Arquitectura de Integración

> **Estado:** Diseño  
> **Versión:** 1.0  
> **Dependencias:** walker3, SessionStore, TaskRegistry  
> **Prioridad:** Alta — afecta la experiencia del agente en cada tool call

---

## 1. Filosofía

Los hooks son un **proxy** entre el agente y el LLM. No tienen lógica pesada, no bloquean sin razón, y solo actúan cuando hay una **sesión walker3 activa**. Si el usuario está explorando código sin un proyecto/sprint asignado, los hooks no existen.

**Regla de oro:** Sin sesión → sin hooks. Con sesión → comportamiento aumentado.

---

## 2. Estados de la sesión

```
                          ┌──────────────┐
               ┌─────────→│ NO EXISTE    │←─────────┐
               │          │ (modo libre) │          │
               │          └──────┬───────┘          │
               │                 │                   │
               │          ┌──────▼───────┐          │
               │          │  SESIÓN      │          │
               │          │  ACTIVA      │          │
               │          │  (modo tarea)│          │
               │          └──────┬───────┘          │
               │                 │                   │
               │    ┌────────────┼────────────┐      │
               │    ▼            ▼            ▼      │
               │ ┌──────┐  ┌────────┐  ┌─────────┐ │
               │ │FRESH │  │ STALE  │  │COMPLETED│ │
               │ │<30min│  │ >2h    │  │sprint   │ │
               │ └──────┘  └────────┘  │cerrado  │ │
               │                       └─────────┘ │
               └────────────────────────────────────┘
```

| Estado | Cuándo | Hook actúa? |
|--------|--------|-------------|
| **NO EXISTE** | No se ha corrido `walker3 status` nunca | ❌ No |
| **ACTIVA FRESH** | Sesión creada hace <30min | ✅ Sí, modo tarea |
| **ACTIVA STALE** | Último tool call hace >2h | ✅ Sí, inyecta confirmación |
| **COMPLETED** | Sprint cerrado, tarea completada | ✅ Sí, inyecta "¿liberar sesión?" |

---

## 3. Comportamiento por tool

### 3.1 Read / Grep / Glob (consultas de código)

**Sin sesión (modo libre):**
```
Hook: no interviene
Agente: usa grep/read/glob normalmente
```

**Con sesión (modo tarea):**
```
Hook: inyecta prompt antes del tool:
  "📌 Para entender el código usa:
   • walker3 <proyecto>/query/cpg --query 'class:X'  ← estructura
   • walker3 <proyecto>/query/ckg --query 'pattern'   ← decisiones
   • walker3 <proyecto>/query/delta --query 'texto'    ← contexto histórico
   En vez de leer archivos raw."

Agente: decide si usa walker3 o sigue con grep.
Hook post: actualiza last_read_at en la sesión.
```

**Edge cases:**
- Archivo no-code (README, .md, imágenes, configs) → CPG no ayuda, hook permite grep sin inyección
- Query CPG devuelve vacío → el agente cae naturalmente a grep (el hook no bloquea)
- Múltiples proyectos en mismo repo → hook inyecta el project_id correcto según sesión

### 3.2 Write / Edit (modificación de archivos)

**Sin sesión (modo libre):**
```
Hook: no interviene
Agente: escribe libremente
```

**Con sesión (modo tarea):**
```
Hook PRE:
  1. Verificar si el archivo está dentro del repo de la sesión activa
  2. Si el archivo NO está en artefactos_impactados de la tarea activa:
     - Registrar como "archivo no planificado" via walker3 task/register_file
     - NO bloquear (solo advertir)
  3. Si el archivo YA está registrado por otra tarea activa:
     - Advertir: "Este archivo también pertenece a {otra tarea}"

Hook POST (solo si éxito):
  1. Consolidar registro: walker3 task/register_file --status modified
  2. Actualizar last_written_at en sesión
```

**Edge cases:**
| # | Caso | Qué hace el hook |
|---|------|-----------------|
| 1 | Archivo nuevo (no existe) | Registrar como nuevo artifact, permitir |
| 2 | Archivo en `.gitignore` / `node_modules/` / `*.pyc` | Ignorar, no registrar |
| 3 | 100 writes al mismo archivo | Deduplicar por (task_id, file_path) |
| 4 | Archivo fuera del repo de la sesión | Inyectar: "¿Cambiaste de proyecto? La sesión apunta a {proyecto}" |
| 5 | Tarea ya completada (PO_ACCEPT) | Inyectar: "Tarea completada. ¿Quieres crear una nueva tarea o liberar la sesión?" |
| 6 | Sprint cerrado | Hook inyecta: "El sprint fue cerrado. ¿Deseas liberar la sesión?" |

### 3.3 Bash (comandos)

| Comando | Hook actúa? | Qué hace |
|---------|------------|----------|
| `git add/commit` | ✅ Pre | Registrar archivos del commit en la tarea activa |
| `git push` | ❌ | Ignorar |
| `pytest`, `qlty`, `ruff` | ✅ Post | Registrar ejecución exitosa en TraceLog |
| `npm install`, `pip install` | ❌ | Ignorar |
| `cp`, `mv`, `touch` archivo | ✅ Pre | Registrar destino como archivo modificado |
| `rm` archivo fuente | ✅ Pre | Registrar eliminación |
| `python3 script.py` | ❌ | Ignorar (el hook no puede saber si es tool del workflow) |
| `walker3 submit`, `transition` | ❌ | Ignorar (el walker ya registra todo via TraceLog) |

### 3.4 Question (mensajes del usuario)

**Con sesión activa y tarea en progreso:**
```
Hook POST (después de Question):
  Almacenar el mensaje del usuario como contexto de la tarea
  via: walker3 task/register_message --text "..."
  
  Límite: últimos 10 mensajes por tarea.
  Solo si hay tarea activa (no en modo exploración).
```

**Edge cases:**
- Mensajes muy largos (>500 chars): truncar
- Mensajes con datos sensibles: ¿cómo detectarlos? No podemos. El usuario debe opt-out.
- Sin tarea activa: no guardar nada.
- Misma tarea, múltiples sesiones (multi-agente): cada agente guarda sus propios mensajes.

---

## 4. Ciclo de vida de la sesión

### 4.1 Inicio de sesión

```
Usuario: "quiero trabajar en delta-skill"
  → Agente corre: walker3 switch delta-skill/taskregistry
  → Walker3 crea SessionRecord en ~/.walker/sessions/{sid}.json
  → Walker3 exporta WALKER_SESSION_ID, WALKER_CHAIN_DIR, WALKER_TASK_ID a env
  → Hook detecta env vars → modo tarea activado
```

### 4.2 Sesión stale (>2h sin actividad)

```
Hook PRE primer tool:
  1. Lee last_read_at de la sesión
  2. Si pasaron >2h:
     → Inyecta: "📌 Tu última actividad fue hace Xh en {proyecto}/{sprint}.
        ¿Sigues trabajando ahí? [sí/no/cambiar]"
  3. Si el agente responde "sí": actualiza last_read_at, continúa
  4. Si responde "no": walker3 session/free → modo libre
  5. Si responde "cambiar": walker3 switch <otro> → nueva sesión
```

### 4.3 Sprint completado (PO_ACCEPT)

```
Hook detecta que la tarea está en estado "completed":
  → Inyecta: "📌 La tarea {TASK-ID} fue completada y aceptada.
     ¿Quieres empezar otra tarea, cerrar el sprint, o liberar la sesión?"
```

### 4.4 Sprint cerrado (SPRINT.CLOSE)

```
Side-effect reindex_delta actualiza el índice.
Hook detecta sprint cerrado:
  → Inyecta: "📌 El sprint {sprint} fue cerrado.
     ¿Deseas liberar la sesión?
     
     Al liberar:
     • Los hooks dejan de intervenir
     • grep/read vuelven a funcionar normalmente
     • Puedes iniciar una nueva sesión cuando quieras con walker3 status"
     
  Si el usuario confirma:
    → walker3 session/terminate
    → Elimina active_session
    → Modo libre
```

### 4.5 Cierre forzado (modo libre)

```bash
walker3 session/free
  → Marca la sesión como "free" (no la elimina)
  → Hooks dejan de intervenir
  → walker3 session/task → vuelve a modo tarea
```

---

## 5. Variables de entorno (cache de sesión)

Para no llamar a `walker3` en cada tool call (~200ms), los hooks leen env vars:

```bash
# Exportadas por walker3 al crear/retomar sesión
WALKER_SESSION_ID="sess-abc123"       # ID de sesión
WALKER_PROJECT_ID="delta-skill"       # Proyecto activo
WALKER_CHAIN_DIR="/path/to/sprint"    # Sprint activo
WALKER_TASK_ID="TASK-012"             # Tarea activa (o vacío)
WALKER_LAST_READ="2026-07-24T10:00"   # Último tool call
WALKER_SPRINT_STATUS="active"         # active | closed
```

El hook solo llama a `walker3` cuando:
- Las env vars no existen (primera vez) → `walker3 session`
- La sesión está stale → `walker3 session/touch`
- Hay que registrar un archivo → `walker3 task/register_file`

---

## 6. Diagrama de flujo completo

```
                    ┌──────────────────────┐
                    │  TOOL CALL (cualquier)│
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │  Leer WALKER_* env   │
                    │  vars o llamar        │
                    │  walker3 session      │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
           ┌────────┤  ¿Hay sesión activa?  │
           │        └──────────┬───────────┘
           │                   │
           │    NO          SI │
           │                   │
     ┌─────▼─────┐    ┌───────▼────────┐
     │ MODO LIBRE │    │  ¿Sesión      │
     │ Sin hooks  │    │  stale >2h?   │
     │ exit 0     │    └───────┬────────┘
     └───────────┘            │
                          SI │     NO
                        ┌────▼──┐  ┌──▼────────────┐
                        │Inyectar│  │  ¿Tool es      │
                        │confirm│  │  Read/Grep?    │
                        │sesión │  └──┬──┬──────┬───┘
                        └───────┘     │  │      │
                                  SI  │  │  NO  │
                              ┌───────▼┐ │ ┌────▼──────┐
                              │Inyectar │ │ │ ¿Tool es  │
                              │prompt   │ │ │Write/Edit?│
                              │walker3  │ │ └──┬──┬─────┘
                              │query    │ │    │  │
                              └────────┘ │ SI │  NO
                                         ┌▼──┐│  │
                                         │Reg││  exit 0
                                         │ist││
                                         │rar││
                                         │arc││
                                         │hiv││
                                         │o  ││
                                         └───┘│
                                              │
                                    ┌─────────▼──┐
                                    │ POST-HOOK  │
                                    │ (si éxito) │
                                    ├────────────┤
                                    │• Registrar │
                                    │  archivo   │
                                    │• Touch     │
                                    │  sesión    │
                                    │• Log       │
                                    └────────────┘
```

---

## 7. Escenarios inversos y de borde

### 7.1 Usuario sin proyecto

```
Usuario: "¿Cómo funciona este código?"
Agente: usa Read/Grep (hooks no intervienen — no hay sesión)
Resultado: ✅ El agente responde sin restricciones
```

### 7.2 Sesión de ayer, olvidé cerrarla

```
Usuario: "Sigue con la tarea de ayer"
Hook: detecta last_read_at > 2h
  → Inyecta: "Tu última actividad fue hace 14h en delta-skill/taskregistry TASK-012.
     ¿Sigues ahí?"
Usuario: "Sí"
Hook: actualiza last_read_at, continúa
Resultado: ✅ Sesión retomada correctamente
```

### 7.3 Sesión de ayer, cambio de contexto

```
Usuario: "Terminemos el diseño del módulo events"
Hook: detecta last_read_at > 2h
  → Inyecta: "Tu última actividad fue hace 14h. ¿Sigues en delta-skill/taskregistry?"
Usuario: "No, ahora estoy en game-reset"
Hook: no hace nada (el usuario no inició walker3 para game-reset)
Agente: empieza a usar grep/read normalmente (modo libre)
Resultado: ✅ El hook no fuerza una sesión incorrecta
```

### 7.4 Write a archivo fuera de artefactos

```
Hook PRE: detecta que "handler.py" no está en artefactos_impactados
  → Registra como archivo no planificado
  → Inyecta: "📌 handler.py no estaba planificado para TASK-012.
     Se ha registrado como archivo adicional.
     Puedes seguirlo editando."
  → NO bloquea
Resultado: ✅ El trabajo continúa, el histórico queda registrado
```

### 7.5 Write a archivo de otra tarea activa

```
Hook PRE: detecta que "events.py" pertenece a TASK-006 (otra tarea activa)
  → Inyecta: "📌 events.py está en TASK-006 (Implementar eventos on:{}).
     ¿Quieres modificarlo igualmente?"
  → NO bloquea
Resultado: ✅ El agente sabe que está tocando código de otra tarea
```

### 7.6 Sprint cerrado, agente sigue escribiendo

```
Hook PRE: detecta que el sprint está "closed"
  → Inyecta: "📌 El sprint fue cerrado. Las tareas están completadas.
     Para seguir trabajando: walker3 switch <nuevo-sprint>
     Para liberar la sesión: walker3 session/free"
  → NO bloquea
Resultado: ✅ El agente puede seguir pero sabe que está en un sprint cerrado
```

### 7.7 Tarea completada, se modifican archivos

```
Hook PRE: detecta que TASK-012 fue aceptada (PO_ACCEPT)
  → Inyecta: "📌 TASK-012 fue completada y aceptada.
     Los cambios que hagas ahora no estarán asociados a ninguna tarea activa.
     ¿Quieres crear una nueva tarea?"
Resultado: ✅ Se evita trabajo huérfano
```

### 7.8 Mismo archivo, dos agentes simultáneos

```
Agente A: modifica events.py → hook registra contra sesión A
Agente B: modifica events.py → hook detecta que events.py fue modificado
  por otra sesión activa hace 5min
  → Inyecta: "⚠️ events.py fue modificado recientemente por otra sesión.
     Coordina con el otro agente antes de sobrescribir."
Resultado: ⚠️ Advertencia, no bloqueo
```

### 7.9 Read sin sesión, pero el usuario quiere contexto del proyecto

```
Usuario: "Dame contexto del proyecto delta-skill"
Hook: no hay sesión → no interviene
Agente: no tiene walker3 → usa grep/read
Resultado: ⚠️ El agente podría beneficiarse de walker3, pero está en modo libre.
         Sería bueno que el mismo agente decida iniciar sesión si necesita
         contexto estructurado.
```

### 7.10 Write masivo (100 archivos en 1 min)

```
Hook PRE: detecta 100 writes rápidos
  → No llama a walker3 en cada uno (seguiría siendo lento)
  → Bufferiza en archivo temporal
  → Flush cada 5 writes o cada 10s
Hook POST: consolida el buffer al final
Resultado: ✅ Sin degradación de performance
```

---

## 8. Archivos hooks propuestos

### 8.1 Pre-tool-use hook (`delta-pre-tool-use.sh`)

```bash
#!/bin/bash
# Proxy: inyecta comportamientos walker3 antes de cada tool call

# 1. Leer sesión desde env (cache) o walker3
SESSION_ID="${WALKER_SESSION_ID:-}"
if [ -z "$SESSION_ID" ]; then
    # No hay sesión → modo libre, salir
    exit 0
fi

# 2. Verificar stale
if is_stale "$WALKER_LAST_READ"; then
    inject_confirmacion "$WALKER_PROJECT_ID" "$WALKER_TASK_ID"
fi

# 3. Según el tool, inyectar prompt
case "$TOOL" in
    Read|Grep|Glob) inject_query_prompt ;;
    Write|Edit)     register_and_check_file "$FILE_PATH" ;;
esac

exit 0
```

### 8.2 Post-tool-use hook (`delta-post-tool-use.sh`)

```bash
#!/bin/bash
# Proxy: registra actividad después de cada tool call

SESSION_ID="${WALKER_SESSION_ID:-}"
[ -z "$SESSION_ID" ] && exit 0  # modo libre

# Solo si el tool tuvo éxito
[ "$EXIT" != "0" ] && exit 0

case "$TOOL" in
    Write|Edit)
        register_file "$FILE_PATH" "modified"
        touch_session  # actualiza last_written_at
        ;;
    Question)
        register_message "$MESSAGE"
        ;;
esac

exit 0
```

---

## 9. Resumen de cambios necesarios en walker3

| Funcionalidad | Estado |
|--------------|--------|
| `walker3 session` devuelve JSON con todos los campos | ✅ Existe |
| `walker3 session/free` — modo libre | ❌ Nuevo |
| `walker3 session/task` — modo tarea | ❌ Nuevo |
| `walker3 session/touch` — actualiza last_read_at | ❌ Nuevo |
| `walker3 task/register_file` — asocia archivo a tarea | ❌ Nuevo |
| `walker3 task/register_message` — guarda mensaje en tarea | ❌ Nuevo |
| `walker3 session/terminate` — elimina sesión | ⚠️ Existe como delete |
| Exportar env vars `WALKER_*` al crear sesión | ❌ Nuevo |
| `last_read_at`, `last_written_at` en SessionRecord | ❌ Nuevo |
| Detectar sprint cerrado en status | ✅ Existe |
| Buffer de writes para no saturar | ❌ Nuevo |
