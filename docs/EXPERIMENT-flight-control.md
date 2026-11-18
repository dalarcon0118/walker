# Experimento — ¿el control de vuelo mejora el trabajo del agente?

- **Estado:** diseño listo para ejecutar
- **Decisión que informa:** ¿los gates y el plan se quedan, se ajustan o se apagan?
- **Unidad de análisis:** una **tarea** resuelta en una sesión nueva (no una sesión,
  no un mensaje).
- **Regla previa:** esto mide valor de producto; los tests del harness ya
  verificaron que el mecanismo funciona.

## Hipótesis

Con el control de vuelo activo (sesión ligada + tarea declarada + alcance +
plan), el agente **cierra más tareas con verificación verde y sin desviarse**,
sin un aumento de fricción o costo que lo haga netamente peor.

Hipótesis nula: no hay diferencia en cierre verificado, o la hay a costa de
fricción/costo.

## Brazos

| Brazo | Configuración | Qué mide |
| :--- | :--- | :--- |
| **T (tratamiento)** | plugin completo: gates de entrada y alcance, plan inyectado, gate de cierre | efecto de las intervenciones |
| **C (control instrumentado)** | plugin activo solo para registro y métricas; intervenciones apagadas: `WALKER_FLIGHT_CONTROL_INTAKE=0`, `_SCOPE=0`, `_GUIDE=0` | línea base comparable (mismo registro, sin guía) |

El control **debe conservar** `session_open`/`session_close`/`session_metrics`;
si se apaga el plugin entero no hay datos y la comparación no existe. Por eso
`_GUIDE=0` (desactiva solo el bloque de plan y el aviso de cierre) es un
prerrequisito.

## Variables controladas

Se fijan y se registran en cada corrida:

- versión de OpenCode (pinneada, misma para ambos brazos);
- modelo y proveedor (de pago, cuota estable; **no** free tier);
- agente (`build`), mismo `AGENTS.md`, mismas permisos;
- catálogo MCP congelado durante el experimento (registrar la lista);
- mismo commit del repo por par de tareas;
- una sesión a la vez por proyecto (el `task_id` del workflow es del proyecto y
  dos sesiones concurrentes se pisan: medido);
- sin editar el plugin durante el experimento (las recargas borran estado);
- mismo modo de harness (server compartido para realismo; no mezclar con
  `--standalone`, que tiene otra interceptación de tools).

## Conjunto de tareas

12–20 tareas reales, pareadas entre brazos (la misma tarea se corre en T y en C),
estratificadas por tipo:

- edición trivial (typo/rename) — mide fricción;
- bugfix con test existente — mide cierre verificado;
- feature chica con criterios escritos;
- refactor acotado — mide alcance;
- consulta (no debe disparar gates) — mide falsos positivos.

Cada tarea se especifica **antes** de correrla: objetivo, criterios de
aceptación verificables, archivos esperados y comando de verificación. Sin
criterios escritos no hay VCR computable.

## Métricas

**Primaria**

- **VCR — tasa de cierre verificado** = tareas cerradas con verificación verde y
  sin escrituras fuera de alcance / tareas declaradas.
  Fuente: registro de sesiones + `session_metrics` + resultado de `run_tests` /
  `verify_predicates`.

**Secundarias de foco**

- tasa de escritura en alcance = 1 − (bloqueos por alcance / escrituras);
- desvío por sesión = archivos fuera de alcance / archivos tocados;
- tasa de binding y cobertura de plan (adopción).

**Secundarias de verificación**

- tasa de verificación (sesiones con escritura que verificaron);
- nivel: `tools` (run_tests/verify_predicates) vs `shell` (pytest suelto);
- tasa de cierre bloqueado y **recuperación** (verificó después del aviso).

**Eficiencia**

- **costo USD por tarea verificada** = Σ `cost_usd` / tareas verificadas;
- tiempo hasta la primera escritura y hasta la primera verificación;
- wall-clock por tarea.

**Guardarraíles**

- recuperación tras una denegación (llamadas hasta la siguiente mutación
  permitida);
- uso de escapes (`INTAKE=0`/`SCOPE=0`/`GUIDE=0`) y eventos `plugin_degraded`;
- rework: tarea reabierta o corregida dentro de 7 días.

## Procedimiento

1. **Piloto:** 5 tareas por brazo para calibrar tiempos y el registro.
2. **Corrida:** 12–20 pares, orden aleatorizado por par (T primero o C primero).
3. **Registro** por corrida: `run_id, task_id, arm, modelo, versión, commit,
   inicio, fin, costo, tokens, escrituras, bloqueos, nivel de verificación,
   VCR, notas`.
4. **Cierre:** cada corrida se cierra con `session_list`/`session_metrics`; si
   falta el evento, se marca como dato faltante (no se imputa).

## Análisis y regla de decisión

- Comparación **pareada** por tarea (misma tarea en T y C): diferencia de VCR,
  mediana de costo por tarea verificada, mediana de recuperación.
- Con n=12–20, reportar medianas y proporciones, no promedios; el intervalo es
  ancho y el resultado es una **ayuda a la decisión**, no una prueba científica.
- **Se quedan los gates** si: VCR mejora ≥ 15 puntos, costo por tarea verificada
  no sube más del 25 %, y la recuperación mediana no empeora.
- **Se ajustan** si el foco mejora pero la fricción o el costo empeoran.
- **Se apagan** si VCR no mejora, o si el valor está solo en continuidad
  (binding/registro) y no en los gates.

## Amenazas a la validez

- **Varianza del modelo:** mitigar con pares y orden aleatorio; no alcanza para
  eliminar el ruido.
- **Cuota del proveedor / free tier:** usar de pago y monitorear errores.
- **Deriva del catálogo de tools:** congelar y registrar.
- **Estado entre sesiones:** una sesión a la vez; el `task_id` del workflow es
  del proyecto.
- **Recargas del plugin:** no editarlo durante el experimento.
- **Evaluador:** la verificación debe ser automática y previa (tests o
  predicados), no juicio del agente.
- **Contaminación:** el agente puede haber visto tareas similares; anotarlo.

## Prerrequisitos técnicos

1. Flag `WALKER_FLIGHT_CONTROL_GUIDE=0` (apaga plan y cierre, conserva métricas).
2. `run_tests` ejecutando la suite (hecho) y devolviendo pass/fail.
3. `session_metrics` emitiendo escrituras, alcance, verificación y costo (hecho,
   falta confirmarlo en una corrida en vivo).
4. Un CSV/planilla de registro y un script mínimo que lo agregue.
