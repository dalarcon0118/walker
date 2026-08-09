"""shared — Servicios transversales del Walker.

No es un bounded context ni un god module.
Contiene utilidades y servicios que no pertenecen a ningún módulo específico.

REGLAS DE MÓDULO (definición formal):
  1. libs NO importa features/. Solo stdlib + libs entre sí (DIP).
  2. Cada paquete tiene UNA responsabilidad transversal (no lógica de negocio).
  3. Si algo pertenece a un feature (cpg, ckg, delta, trace, spring), vive en
     features/<feature>/ y NO en libs/. libs/ solo hostea lo REUTILIZADO por
     ≥2 módulos o que es infraestructura pura compartida.
  4. Los events de dominio viven en features/<feature>/domain/events.py
     (NO en libs/events — eliminado por dead code).

Catálogo de paquetes:
  - path_resolver   → resolución de paths del walker (proyectos, data, cpg, ckg)
  - shared_domain   → value objects cross-feature (ExecutionContext, TaskResult, ...)
  - context         → contrato neutro de providers (DIP): ABC + piezas + formaters
  - cognitive_graph → infra compartida de índices (storage + orquestación CQRS)
  - infrastructure  → adapters de lectura (CognitiveGraphSearcher, CQRS read-only)
  - memories        → MemoryQueryService (fachada de consulta de las 4 memorias)
  - router          → Router CLI reutilizable tipo NestJS
  - mcp_server      → servidor MCP compartido (ToolRegistry + registro de tools)
  - result_types    → tipos de resultado (safe/is_successful) para convention-001
"""
from libs.path_resolver import (
    # Paths base
    get_projects_dir,
    get_project_dir,
    get_delta_dir,
    get_sprint_dir,
    get_data_dir,
    get_data_path,
    get_state_path,
    # Project manifest
    get_manifest_path,
    read_project_manifest,
    get_repo_path,
    get_code_paths,
    resolve_source_code_dirs,
    # Chain dir resolution
    resolve_project_root,
    resolve_project_id,
    resolve_real_project_dir,
    resolve_data_path,
    resolve_trace_path,
    # CPG paths
    get_cpg_path,
    get_ckg_path,
)
