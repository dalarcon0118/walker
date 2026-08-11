# language: es
# tests/features/cpg_indexed.feature
#
# Feature: Certificación del indexado CPG (SPEC-002 G-W1).
#
# Como mantenedor del walker
# Quiero que el build exitoso de update_cpg emita un evento cpg_indexed
# al TraceGraph con fingerprint_match, stats y quality (SPEC-002)
# Para que el tracer responda "¿está este CPG certificado tras el build?"
# sin cruzar side_effect_execute + file_impact del watcher.

Característica: Evento cpg_indexed post-build del CPG
  Como mantenedor del walker
  Quiero que update_cpg certifique su espejo con un evento cpg_indexed
  Para que el TraceGraph tenga la certificación del indexado

  @cpg-indexed
  Escenario: Update CPG exitoso emite cpg_indexed con certificación del espejo
    Dado SideEffectEngine se construye con un tracer
    Y registro update_cpg con fingerprint verify true y quality ok
    Cuando ejecuto update_cpg con un ExecutionContext con chain_dir
    Entonces se emite un evento cpg_indexed
    Y el evento cpg_indexed lleva scope "code_index" y quality_passed true
    Y el evento cpg_indexed lleva fingerprint_match true
    Y el evento cpg_indexed lleva source "walker" y task_id "T1"
    Y el evento cpg_indexed lleva files_updated y total_nodes y total_edges
    Y el evento cpg_indexed lleva source_fingerprint

  @cpg-indexed
  Escenario: Build del CPG con fingerprint sin verificar emite cpg_indexed con fingerprint_match false
    Dado SideEffectEngine se construye con un tracer
    Y registro update_cpg con fingerprint verify false y quality ok
    Cuando ejecuto update_cpg con un ExecutionContext con chain_dir
    Entonces se emite un evento cpg_indexed
    Y el evento cpg_indexed lleva fingerprint_match false

  @cpg-indexed
  Escenario: Update CPG fallido NO emite cpg_indexed (solo side_effect_execute)
    Dado SideEffectEngine se construye con un tracer
    Y registro update_cpg que lanza
    Cuando ejecuto update_cpg con un ExecutionContext con chain_dir
    Entonces se emite un evento side_effect_execute
    Y NO se emite ningún evento cpg_indexed

  @cpg-indexed
  Escenario: Sin tracer no se emite ningún evento cpg_indexed (degradación segura)
    Dado SideEffectEngine se construye sin tracer
    Y registro update_cpg con fingerprint verify true y quality ok
    Cuando ejecuto update_cpg con un ExecutionContext con chain_dir
    Entonces no se emite ningún evento cpg_indexed