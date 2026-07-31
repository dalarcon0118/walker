"""shared — Servicios transversales del Walker.

No es un bounded context ni un god module.
Contiene utilidades y servicios que no pertenecen a ningún módulo específico.

Servicios:
  - path_resolver.py  → resolución de paths del walker
  - (futuro) metrics  → integración con qlty
  - (futuro) config   → configuración transversal
"""
from _walker.shared.path_resolver import (
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
    # Task manifests
    get_task_manifest_path,
    load_task_manifest,
)
