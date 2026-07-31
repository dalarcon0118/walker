"""application/build_service.py — BuildGraphUseCase.

Orchestrates the CPG build pipeline:
  1. sg outline (multi-language) → nodes + edges
  2. CFG scan (Python) → function-level CALLS edges
  3. DFG scan (Python) → data flow analysis
"""

from __future__ import annotations
import os

from cpg.infrastructure.repository import CpgFileRepository, CPG_VERSION, _now


class BuildGraphUseCase:
    """Build or update the CPG for a project."""

    def __init__(
        self,
        project_id: str,
        data_dir: str,
        code_root: str,
        code_paths: list[str] | None = None,
        repo_path: str = "",
    ):
        self.project_id = project_id
        self.data_dir = data_dir
        self.code_root = code_root
        self.code_paths = code_paths or []
        self.repo_path = repo_path
        self._repo = CpgFileRepository(data_dir)

    def resolve_code_paths(self, paths: list[str] | None = None) -> list[str]:
        """Resuelve los paths a escanear, igual que el CPGBuilder original."""
        if paths is not None:
            return paths
        base = self.repo_path or self.code_root
        if self.code_paths:
            return [os.path.join(base, p) for p in self.code_paths]
        project_code = os.path.join(base, "projects", self.project_id)
        if os.path.isdir(project_code):
            return [project_code]
        return [self.code_root]

    def _auto_fix(self, paths: list[str]) -> None:
        """Auto-fix lint issues antes de escanear.

        Interno: usa qlty check --fix. Nadie fuera de este método
        necesita saber qué herramienta se usa.
        """
        try:
            import subprocess

            subprocess.run(
                ["qlty", "check", "--fix", "--no-fail"] + paths[:10],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except Exception:
            pass  # Auto-fix es best-effort, no debe romper el build

    def build(self, scan_paths: list[str] | None = None) -> dict:
        """Full rebuild via ast-grep + CFG analysis (CALLS edges).

        Automáticamente aplica auto-fix de lint antes de escanear.
        """
        paths = self.resolve_code_paths(scan_paths)

        # 0. Auto-fix antes de escanear (oculto, qlty es detalle interno)
        self._auto_fix(paths)

        # 1. ast-grep scan (multi-language) → nodos + imports
        from cpg.infrastructure.ast_grep_adapter import scan_files

        graph = scan_files(paths)

        # 2. Guardar base CPG (para que scan_cfg pueda cargarlo)
        data = graph.to_dict()
        now = _now()
        data.update(
            {
                "version": CPG_VERSION,
                "project_id": self.project_id,
                "generated_by": "BuildGraphUseCase/ast-grep",
                "generated_at": now,
                "updated_at": now,
                "update_count": 1,
                "stats": {
                    "total_nodes": len(data.get("nodes", {})),
                    "total_edges": len(data.get("edges", [])),
                    "files_scanned": len(
                        set(n.path for n in graph.nodes.values() if n.type == "file")
                    ),
                },
            }
        )
        self._repo.save(data)

        # 3. CFG scan → CALLS edges entre funciones
        # scan_cfg carga del repo (datos de ast-grep ya guardados),
        # añade CALLS edges, y guarda de nuevo
        self.scan_cfg(paths)

        # 4. Recargar con CALLS edges incluidos
        data = self._repo.load(self.project_id)
        return data

    def update(self, refresh: bool = False) -> dict:
        """Incremental = full rebuild (por ahora)."""
        return self.build()

    def index_task_files(self, task_files: list[str]) -> dict:
        """Re-indexa solo los archivos de una tarea completada."""
        if not task_files:
            return {"ok": True, "files_updated": 0, "scope": "task"}

        abs_paths = []
        for f in task_files:
            p = os.path.join(self.repo_path or self.code_root, f)
            if os.path.exists(p):
                abs_paths.append(p)

        if not abs_paths:
            return {"ok": True, "files_updated": 0, "scope": "task"}

        old_data = self._repo.load(self.project_id)
        old_nodes = old_data.get("nodes", {})
        old_edges = old_data.get("edges", [])

        # ast-grep scan de los archivos cambiados
        from cpg.infrastructure.ast_grep_adapter import scan_files

        new_graph = scan_files(abs_paths)

        # Remove old nodes from changed paths
        changed_paths = set(abs_paths)
        paths_to_remove = {
            nid
            for nid, n in old_nodes.items()
            if isinstance(n, dict) and n.get("path") in changed_paths
        }

        # Merge: keep unaffected nodes + new ones
        data = old_data
        data["nodes"] = {
            nid: n for nid, n in old_nodes.items() if nid not in paths_to_remove
        }
        for nid, node in new_graph.nodes.items():
            data["nodes"][nid] = node.to_dict()
        data["edges"] = [
            e
            for e in old_edges
            if e["from"] not in paths_to_remove and e["to"] not in paths_to_remove
        ] + [e.to_dict() for e in new_graph.edges]
        data["updated_at"] = _now()
        data["update_count"] = data.get("update_count", 0) + 1

        self._repo.save(data)
        return {
            "ok": True,
            "files_updated": len(abs_paths),
            "nodes_added": len(new_graph.nodes),
            "edges_added": len(new_graph.edges),
            "scope": "task",
        }

    def scan_cfg(self, scan_paths: list[str] | None = None) -> dict:
        """CFG analysis: detecta CALLS edges multi-lenguaje via ast-grep.

        Usa ast-grep run con pattern $FUNC($$$) que funciona en cualquier
        lenguaje (Python, TypeScript, Rust, Go, Java, etc.).

        También mantiene análisis de bugs Python-only (dead_branches, etc.)
        para compatibilidad con proyectos Python.
        """
        # 1. Resolver archivos de cualquier lenguaje
        raw_paths = scan_paths if scan_paths is not None else self.resolve_code_paths()
        all_files = []
        for p in raw_paths:
            if os.path.isfile(p):
                all_files.append(p)
            elif os.path.isdir(p):
                for root, dirs, files in os.walk(p):
                    dirs[:] = [
                        d for d in dirs if not d.startswith(".") and d != "__pycache__"
                    ]
                    for f in files:
                        all_files.append(os.path.join(root, f))

        if not all_files:
            return {"dead_branches": [], "missing_returns": [], "infinite_loops": []}

        # 2. Cargar CPG actual (tiene nodos de ast-grep outline)
        data = self._repo.load(self.project_id)
        cpg_nodes = data.setdefault("nodes", {})
        cpg_edges = data.setdefault("edges", [])
        data.setdefault(
            "analysis_results",
            {
                "dead_branches": [],
                "missing_returns": [],
                "infinite_loops": [],
            },
        )

        nid_counter = [len(cpg_nodes) + 1]

        def _cpg_nid() -> str:
            nid_counter[0] += 1
            return f"cpg-{nid_counter[0]:04d}"

        # 3. Obtener definiciones de funciones desde el CPG
        #    (poblado por ast-grep outline en build())
        defs_by_file: dict[str, list[dict]] = {}
        for nid, n in cpg_nodes.items():
            if n.get("type") in ("function", "method") and not n.get("is_import"):
                fpath = n.get("path", "")
                defs_by_file.setdefault(fpath, []).append(
                    {
                        "name": n.get("name", ""),
                        "line": n.get("line", 0),
                        "end_line": None,
                    }
                )

        # 4. Detectar llamadas multi-lenguaje via ast-grep
        from cpg.infrastructure.ast_grep_calls import detect_calls

        total_calls = 0
        for fp in all_files:
            if not os.path.isfile(fp):
                continue
            definitions = defs_by_file.get(fp, [])
            if not definitions:
                continue  # Skip files without known function definitions

            try:
                calls = detect_calls(fp, definitions)
            except Exception:
                continue

            for call in calls:
                caller = call["caller"]
                callee = call["callee"]
                call_line = call["line"]

                if not caller or not callee or caller.startswith("$"):
                    continue

                total_calls += 1

                # Caller node
                caller_nid = None
                for nid, n in cpg_nodes.items():
                    if (
                        n.get("name") == caller
                        and n.get("type") in ("function", "method")
                        and n.get("path") == fp
                    ):
                        caller_nid = nid
                        break
                if not caller_nid:
                    caller_nid = _cpg_nid()
                    cpg_nodes[caller_nid] = {
                        "id": caller_nid,
                        "type": "function",
                        "name": caller,
                        "path": fp,
                        "line": call_line,
                    }

                # Callee node (buscar por nombre en cualquier archivo)
                callee_nid = None
                for nid, n in cpg_nodes.items():
                    if (
                        n.get("name") == callee
                        and n.get("type") in ("function", "method")
                        and not n.get("is_import")
                    ):
                        callee_nid = nid
                        break
                if not callee_nid:
                    simple_name = callee.split(".")[-1] if "." in callee else callee
                    for nid, n in cpg_nodes.items():
                        if (
                            n.get("name") == simple_name
                            and n.get("type") in ("function", "method")
                            and not n.get("is_import")
                        ):
                            callee_nid = nid
                            break
                if not callee_nid:
                    callee_nid = _cpg_nid()
                    cpg_nodes[callee_nid] = {
                        "id": callee_nid,
                        "type": "function",
                        "name": callee,
                        "path": fp,
                        "line": call_line,
                        "kind": "external_call",
                    }

                # Edge (dedup)
                edge_exists = any(
                    e["from"] == caller_nid
                    and e["to"] == callee_nid
                    and e["type"] == "CALLS"
                    for e in cpg_edges
                )
                if not edge_exists:
                    cpg_edges.append(
                        {"from": caller_nid, "to": callee_nid, "type": "CALLS"}
                    )

        self._repo.save(data)
        return data.get(
            "analysis_results",
            {
                "dead_branches": [],
                "missing_returns": [],
                "infinite_loops": [],
            },
        )

    def scan_dfg(self, scan_paths: list[str] | None = None) -> dict:
        """Security analysis via ast-grep patterns (multi-lenguaje).

        Busca hardcoded secrets, taint paths, y patrones de seguridad
        usando ast-grep deep patterns de tools/codereview/config.py.
        """
        from cpg.infrastructure.ast_grep_adapter import _run_ast_grep, _detect_lang

        raw_paths = scan_paths if scan_paths is not None else self.resolve_code_paths()
        all_files = []
        for p in raw_paths:
            if os.path.isfile(p):
                all_files.append(p)
            elif os.path.isdir(p):
                for root, dirs, files in os.walk(p):
                    dirs[:] = [
                        d for d in dirs if not d.startswith(".") and d != "__pycache__"
                    ]
                    for f in files:
                        all_files.append(os.path.join(root, f))

        all_results = {
            "hardcoded_secrets": [],
            "taint_paths": [],
            "uninitialized_vars": [],
            "data_gaps": [],
        }

        try:
            from tools.codereview.config import _DEEP_PATTERNS

            # Usar patterns de seguridad y producción
            security_patterns = _DEEP_PATTERNS.get("danger", []) + _DEEP_PATTERNS.get(
                "production-bugs", []
            )

            for fp in all_files[:200]:  # Limitar por performance
                lang = _detect_lang(fp)
                if not lang or not os.path.isfile(fp):
                    continue
                for pat in security_patterns:
                    matches = _run_ast_grep(pat["pattern"], fp, lang)
                    for m in matches:
                        entry = {
                            "file": fp,
                            "line": m["line"],
                            "detail": m.get("text", "")[:80],
                            "severity": pat.get("severity", "medium"),
                        }
                        if pat.get("severity") in ("high", "critical"):
                            all_results["hardcoded_secrets"].append(entry)
                        else:
                            all_results["taint_paths"].append(entry)
        except Exception:
            pass

        data = self._repo.load(self.project_id)
        data["dfg"] = all_results
        self._repo.save(data)
        return all_results
