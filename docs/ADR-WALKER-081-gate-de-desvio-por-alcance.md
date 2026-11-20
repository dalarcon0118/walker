# ADR-WALKER-081 — Gate de desvío por alcance

- **Estado:** reemplazado por [FLIGHT-CONTROL.md](FLIGHT-CONTROL.md) (v4, 2026-11-20)
- **Fecha:** 2026-11-18
- **Relacionado:** ADR-WALKER-080 (plan de vuelo)

> **Vigente:** la guía operativa del flight control es `docs/FLIGHT-CONTROL.md`.
> Este ADR se conserva como registro histórico; no define la política vigente.

## Contexto

El plan de vuelo define `scope` (los archivos que la sesión declaró tocar) y el
bloque inyectado muestra el desvío cuando aparece. Pero mostrarlo no cambia el
comportamiento: el agente puede escribir fuera del alcance, el bloque lo señala
y el trabajo sigue. En la práctica eso deja el "foco" como una sugerencia.

## Decisión

Un gate de desvío rechaza las escrituras (`edit`/`write`) fuera de `scope`:

- **Solo si hay alcance declarado.** Un plan sin `scope` no bloquea nada: el
  gate no inventa un alcance.
- **Mensaje accionable.** Nombra el archivo, el alcance vigente y el comando
  exacto para ampliarlo con `session_plan mode=set scope=[...]`. No es una
  advertencia: es un `Tool.Error`, el mismo mecanismo que demostró cambiar el
  comportamiento.
- **Ampliar es explícito y auditable.** No hay auto-ampliación: si el archivo
  corresponde, el agente actualiza el plan y queda registrado en el delta/trace.
- **Fail-open.** Si el plan no se puede leer (walker caído, timeout), se permite
  la escritura: el protocolo es guía, no un requisito de funcionamiento.
- **Default ON**, `WALKER_FLIGHT_CONTROL_SCOPE=0` como escape.

### Matching de alcance

- Igualdad exacta.
- Sufijo de path con límite (`f.endsWith("/" + scope)`), que es lo que permite
  declarar rutas relativas al proyecto aunque el archivo llegue absoluto.
- Directorio declarado (sin extensión o con `/` final): prefijo con límite.

## Invariantes

- **INV-1:** toda escritura está dentro del alcance o el alcance se amplió
  explícitamente antes.
- **INV-2:** el gate no modifica el plan por su cuenta.
- **INV-3:** sin alcance declarado no hay bloqueo (el plan manda).
- **INV-4:** el rechazo es determinista y no depende del modelo.

## Limitaciones conocidas

- **`shell` no se inspecciona por path.** Un `printf > archivo` fuera de alcance
  no lo bloquea este gate (sí marca `wrote` para el cierre). Atribuir paths en
  comandos arbitrarios es heurístico y se dejó fuera a propósito.
- **El alcance es estático por tarea.** Cambiar de objetivo exige ampliar o
  reemplazar `scope`; eso es intencional: el cambio queda registrado.
- **Caché de 10s** del plan: se invalida al correr `session_plan`,
  `implement_task` o `session_bind` por CLI/tool, pero una edición externa al
  registro puede tardar hasta 10s en verse.
- **Sesiones que editan otro repo** (como esta: sesión en `game_reset` editando
  `walker`) deben declarar el alcance relativo al proyecto del repo editado o
  usar un directorio; el matching por sufijo lo cubre.

## Consecuencias

- El plan deja de ser un tablero y pasa a ser el contrato de la sesión.
- El agente tiene un camino claro para corregir el alcance sin adivinar.
- Si el alcance se declara mal, la fricción es un round trip; el costo es el
  mismo patrón que los otros gates y queda auditado.
