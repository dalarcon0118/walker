# language: es
# tests/features/cpg_project_integration.feature
#
# Feature: Integración CPG ↔ Project (regresión ADR-015).
# La cadena pública es ProjectService → CPGBuilder. Los builders NO deben
# depender de atributos directos (project.repo_path) sino de manifest.repo_path.
# REGLA DE ORO: los tests del CPG cubren la cadena PÚBLICA.

Característica: Integración del CPG con el proyecto registrado
  Como arquitecto del módulo CPG
  Quiero que CPGBuilder resuelva paths desde el manifest del proyecto
  Para no romper la cadena pública ProjectService → CPGBuilder

  @regresión
  Escenario: from_chain_dir puebla el manifest
    Dado un proyecto registrado con repo_path y chain_dir
    Cuando se resuelve el proyecto desde chain_dir
    Entonces el manifest expone el repo_path
    Y el manifest expone el nombre declarado
    Y code_paths incluye los paths declarados

  @regresión
  Escenario: CPGBuilder usa repo_path del manifest
    Dado un proyecto registrado con repo_path y chain_dir
    Cuando se instancia un CPGBuilder con el repo_path
    Entonces repo_path del builder coincide con el repo real
    Y code_root del builder es un directorio existente

  @regresión
  Escenario: La cadena completa construye el CPG
    Dado un proyecto registrado con repo_path y chain_dir
    Cuando se ejecuta ProjectService → CPGBuilder → build
    Entonces el build devuelve nodos (total_nodes > 0)