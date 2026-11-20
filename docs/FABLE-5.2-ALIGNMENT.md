# Fable 5.2 y Walker — qué internaliza el modelo y qué debe quedar en el harness

> Investigación: 2026-11-20. Fuentes: anuncio de Anthropic (2026-11-09), docs
> de plataforma (`fable-5-2/overview`, `whats-new`, `prompting-claude-fable-5-2`),
> blog de multi-agente (2026-11-09) y de verificación (2026-11-16).
> Este documento es una **propuesta de alineación**, no un cambio de contrato.

## 1. Qué es Fable 5.2 (hechos verificables)

- Mismo modelo subyacente que Mythos 5.2; Mythos es la variante sin los
  clasificadores de ciber/biología, disponible sólo a organizaciones verificadas.
- **1M de contexto**, **128k de salida**, *adaptive thinking* siempre encendido y
  un parámetro `effort` con cinco niveles (`low`, `medium`, `high`, `xhigh`,
  `max`). El default de la API es `high`.
- Precio: **$8 / MTok input y $40 / MTok output** (Fable 5.1: $10/$50). Fast mode
  duplica el precio y acelera hasta 2,5×.
- **Preserved thinking**: los bloques de razonamiento están atados al prefijo de
  la conversación. Si el harness cambia `system`, `tools` o un mensaje anterior,
  la API devuelve **400** (`The block is bound to a different conversation`), o
  los descarta con `drop_block` — y el modelo vuelve a pensar, con más tokens.
- **Compaction on demand** y **mid-conversation tool changes**: el harness puede
  resumir y cambiar tools sin reescribir el historial.
- Benchmarks destacados (tabla del anuncio):

| Eval | Fable 5.2 | Opus 5.5 | GPT-6 Astra | Gemini 4 Carbon |
| :-- | --: | --: | --: | --: |
| FrontierCode 2.0 | **49,1%** (xhigh) / 45,3% (high) | 45,5% (max) | 42,8% | 41,1% |
| FrontierSWE v2 | **69,9%** | 62,3% | 65,5% | 61,2% |
| Terminal-Bench-Science | **74,6%** | 58,7% | 68,1% | 64,8% |
| WANDR (búsqueda agéntica) | **82,1%** | 72,3% | — | — |
| OSWorld 2.1 | **89,3%** | 87,2% | 79,5% | 81,5% |
| RLI (trabajo remoto) | **41,3%** | 21,3% | 20,8% | — |
| AA-Briefcase (Elo) | 1785 | **1800** | 1569 | 1611 |

Lectura: no es "mejor en todo". Es mejor en **trabajo agéntico largo, código y
ciencia**, y a `high` empata con Opus 5.5 `max` a **la mitad del costo por
tarea**. En conocimiento general (Elo) Opus sigue arriba. Anthropic recomienda
empezar en Opus 5.5 y subir a Fable sólo para lo más difícil.

## 2. Lo que el modelo ya trae "adentro"

Fable internaliza lo genérico: planificación multistep, uso de herramientas,
navegación de código, autocrítica, calibración de esfuerzo, compactación,
delegación y estilo. Eso no significa que sea autosuficiente; significa que
**el andamiaje genérico pasó de ayuda a impuesto**.

Evidencia de comportamiento (docs de prompting):

- A `low`/`medium` **corta el turno antes de terminar**; con el texto de
  "Finishing work" se reducen a la mitad los cierres prematuros, +30% de output.
- A `high` **piensa antes de casi toda respuesta**; pedirle "pensá menos" ya no
  funciona. Se baja el `effort`, no se pide por prompt.
- **Delega según el harness**: con una tool de subagentes mínima casi no delega;
  con un prompt que invita, delega mucho y los subagentes dominan el costo.
- **Sigue las reglas de estilo al pie de la letra**: un límite duro de oraciones
  vuelve la prosa entrecortada.
- **La cautela se acumula**: memorias y notas con "nunca" o "pedí aprobación"
  hacen que pida permiso de más.
- **Ownership importa**: decir "el código es tuyo" sube la consolidación de
  duplicados de <10% a ~75% de las corridas, con +70–80% de output.
- **Un budget de tokens mostrado en el prompt cambia cuánto gasta**: quitarlo
  baja el costo sin perder calidad.

## 3. El experimento que valida el sustrato

El blog de multi-agente es la mejor noticia para Walker: 1.000 agentes con
**shell + editor + Git + Postgres**, sin plataforma de dominio y sin dirección
humana, formalizaron Fermat en 28,7 h, con 1/3 de los tokens y un proof 7× más
corto que la formalización anterior. En los primeros 10 minutos el lead creó
**cinco tablas**: `announce`, `assign`, `notes`, `subtasks`, `landed`.

Conclusión: cuando el modelo es suficientemente fuerte, **no necesita que le
enseñen a coordinar; necesita un estado compartido donde coordinar**. El delta de
Walker (spring/spec/task/evidence) está cerca de eso, pero hoy se usa como una
secuencia obligatoria de gates, no como un tablero de trabajo.

## 4. Dónde Walker sigue siendo insustituible

1. **Verdad del proyecto**: decisiones, invariantes, contrato de aceptación,
   ownership y convenciones que el modelo no puede inferir.
2. **Verificación externa**: el propio anuncio reporta que modelos anteriores
   sobreestimaban la severidad de 1 de cada 20 hallazgos, y la auditoría de
   comportamiento mide "falsely claiming to have completed a task". El modelo no
   puede ser el único juez de su trabajo.
3. **Evidencia durable**: los bloques de thinking mueren con el modelo y la
   conversación; una decisión con statement+rationale sobrevive. El CKG ya
   guarda eso, no razonamiento.
4. **Política y sandbox**: scope, mutaciones de shell, límites de escritura. El
   modelo trae clasificadores de seguridad, pero el sandbox es tuyo.
5. **Coordinación multi-agente**: claim/assign/landed y jerarquía de sesiones.

## 5. Los cuatro choques con el harness actual

### 5.1 Inyección vs. preserved thinking

El plugin hace `event.system.push(...)` en cada hook de contexto. Si OpenCode
reconstruye el `system` en cada request, **cada inyección invalida los bloques de
thinking**: 400 o drop + re-pensar. Es el choque más caro y el más fácil de no
ver, porque sólo se manifiesta con modelos que preservan thinking.

Regla: **historial append-only**. Lo que cambia por turno va como mensaje de
sistema *turn-scoped* al final (o como recordatorio en el turno), no reescribiendo
el system. Verificar cómo arma el request OpenCode antes de tocar nada.

### 5.2 Effort vs. ceremonia de nodos

El workflow pide 4 `submit` + 4 `transition` (~1–2 s cada uno) para llegar a
REVIEW. Con Fable a `high`, el pensamiento ya ocurre; el peaje agrega tokens y
latencia sin agregar juicio. Los nodos deben quedar como **checklist de
artefactos** (el `design_brief` con alternativas y descarte sigue valiendo),
no como una fila de formularios.

### 5.3 Corte temprano vs. "done"

Fable corta antes de terminar a `low`/`medium`, sobre todo en tareas largas. El
cierre de Walker ya nudga verificación una vez; el patrón correcto es:
**"done" definido por predicados + tests**, un re-prompt si quedan criterios sin
verificar, y a la tercera ronda, humano. No prosa.

### 5.4 Delegación sin jerarquía

OpenCode tiene subagentes, y con Fable el uso depende del harness. Walker no
registra `parent_session` ni costo por subagente: no se puede decidir si delegar
rinde. El experimento de 1.000 agentes sugiere que el valor está en el tablero
compartido, no en la cantidad de agentes.

## 6. Plan de alineación (F5)

### F5.1 · Inyección compatible con thinking preservado
- Medir cómo OpenCode construye `system`/`tools` por turno.
- Pasar las inyecciones a mensajes turn-scoped; si no hay soporte, `drop_block`
  como stopgap y medir el costo en output.
- **Aceptación:** 20 turnos con Fable sin 400 por prefijo; cache hit sin caída;
  output +≤10%.

### F5.2 · Effort como presupuesto
- `brief` devuelve `effort` sugerido por nodo: `xhigh` en DESIGN/REVIEW, `high`
  en IMPLEMENT, `medium` en bookkeeping.
- Quitar del prompt las líneas de "pensá menos".
- **Aceptación:** mismo gate-pass con ≤70% de tokens en tareas rutinarias.

### F5.3 · Verificación de primera clase
- Stamp de verificación en el commit (`tests|predicates|e2e|none`), consultable.
- Regla de 3 rondas → humano; gates nuevos en modo `observe` antes de `enforce`.
- Los checks no pueden modificar sus propias reglas (leer de la rama base).
- E2E, property y differential como tipos de primer nivel, no sólo pytest.
- **Aceptación:** 0 cierres sin stamp; 1er intento ≥70%; máximo 3 rondas.

### F5.4 · Delta como tablero de coordinación
- `assign` / `claim` / `notes` / `landed` para subagentes; `parent_session` en el
  trace; costo por subagente; política de delegación en la descripción de la tool.
- **Aceptación:** una tarea con 3 subagentes deja jerarquía, costo y sin
  duplicación de trabajo.

### F5.5 · Podar el harness
- Revisar `AGENTS.md`, `project.md` y el CKG: quitar "pensá menos", límites de
  estilo duros y cautelas vencidas; declarar ownership por proyecto; no mostrar
  budgets de tokens.
- **Aceptación:** diff del prompt con justificación por línea; medir output.

### Medición
A/B de la misma tarea con el harness actual y con F5 (sólo verdad del proyecto),
midiendo tokens, tiempo, primer intento y gate-pass. Sin A/B no se sabe si el
andamiaje ayuda o estorba con un modelo más fuerte.

## 7. Qué NO copiar

- La ceremonia de 4 submits como sustituto del pensamiento.
- Coaching genérico ("sé riguroso", "pensá paso a paso"): ya está en el modelo.
- Límites duros de estilo: el modelo los obedece y la prosa se rompe.
- Cautelas acumuladas sin fecha de revisión: se siguen para siempre.
- Mostrar el budget de tokens en el prompt.
- Confiar en la verificación que el propio cambio produce.

## 8. failure_scenarios

1. **Cambiar la inyección a mensajes turn-scoped sin verificar OpenCode** → el
   prompt deja de llegar o se duplica. Mitigación: feature flag y prueba de 20
   turnos con proveedor real. Detección: 400 por prefijo + trace `injection_mode`.
2. **Bajar la ceremonia de nodos y perder el gate de diseño** → specs vacías.
   Mitigación: `ARTIFACT_GATES` sigue validando forma. Detección: specs con <2
   opciones o sin descarte.
3. **Delegar más con Fable y multiplicar el costo** → sesiones caras sin mejora.
   Mitigación: política en la tool + cap por sesión + costo trazado. Detección:
   costo por tarea y % de tokens en subagentes.
4. **Podar reglas y perder un invariante** → regresión silenciosa. Mitigación:
   diff por línea con justificación y revisión humana. Detección: reverts.
5. **Aceptar el "verde" del modelo** → falso positivo. Mitigación: checks
   inmutables + stamp + 3 rondas. Detección: tasa de revert de cambios verdes.
