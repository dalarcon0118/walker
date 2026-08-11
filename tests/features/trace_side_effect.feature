# language: es
# tests/features/trace_side_effect.feature
#
# Feature: Trazabilidad de side-effects del workflow (SPEC-002 G-W1)
#
# Como mantenedor del walker
# Quiero que cada side-effect del workflow (update_cpg, index_delta, save_ckg,
# run_script, emit_event, ...) registre un evento side_effect_execute en el
# TraceGraph al ejecutarse
# Para poder reconstruir la causalidad: qué efecto corrió, qué canal lo
# invocó, cuánto tardó y si terminó ok/failed — sin acoplar los handlers.

Característica: Trazabilidad de side-effects del workflow
  Como mantenedor del walker
  Quiero que SideEffectEngine emita un evento side_effect_execute por cada efecto
  Para que el tracer sea la fuente de causalidad de la ejecución del workflow

  @trace-side-effect
  Escenario: Un side-effect exitoso emite su evento de traza
    Dado SideEffectEngine se construye con un tracer
    Y registro un side-effect exitoso "ok_effect"
    Cuando ejecuto ese side-effect con un ExecutionContext con chain_dir
    Entonces se emite un evento side_effect_execute
    Y el evento lleva tool "ok_effect" y ok true
    Y el evento lleva channel "workflow" y scope "tracking"
    Y el evento lleva source "walker"
    Y el evento lleva task_id "T1"
    Y el evento lleva duration_ms y signals_keys
    Y el evento NO incluye el campo "data" en signals_keys
    Y el primer evento lleva idempotent false

  @trace-side-effect
  Escenario: Un side-effect fallido emite su evento de traza con ok false
    Dado SideEffectEngine se construye con un tracer
    Y registro un side-effect que lanza "fail_effect"
    Cuando ejecuto ese side-effect "fail_effect" con un ExecutionContext con chain_dir
    Entonces se emite un evento side_effect_execute
    Y el evento lleva tool "fail_effect" y ok false
    Y el evento lleva channel "workflow" y scope "tracking"
    Y el evento lleva source "walker"
    Y el evento lleva task_id "T1"
    Y el primer evento lleva idempotent false

  @trace-side-effect
  Escenario: Un side-effect idempotente emite su evento con idempotent true
    Dado SideEffectEngine se construye con un tracer
    Y registro un side-effect exitoso "ok_effect"
    Y ejecuto ese side-effect con idempotency_key "k1"
    Cuando ejecuto el mismo side-effect de nuevo con idempotency_key "k1"
    Entonces se emiten 2 eventos side_effect_execute
    Y el primer evento lleva idempotent false
    Y el segundo evento lleva idempotent true

  @trace-side-effect
  Escenario: Sin tracer no se emite ningún evento (degradación segura)
    Dado SideEffectEngine se construye sin tracer
    Cuando ejecuto un side-effect "ok_effect" sin tracer
    Entonces no se emite ningún evento side_effect_execute