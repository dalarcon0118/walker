"""application/build_service.py — BuildGraphUseCase.

Orchestrates the CPG build pipeline:
  1. sg outline (multi-language) → nodes + edges
  2. CFG scan (Python) → function-level CALLS edges
  3. DFG scan (Python) → data flow analysis
"""

from __future__ import annotations
import os
import sys
from typing import Any

from features.code_analysis.infrastructure.repository import CpgFileRepository, CPG_VERSION, _now



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
        # SSOT de exclusion: .walkerignore del proyecto (si existe)
        self._ignore = self._load_ignore()

    def _load_ignore(self) -> Any | None:
        """Carga IgnoreRules desde .walkerignore del proyecto.

        El watcher ya respeta .walkerignore; el CPG debe hacer lo mismo
        para que el build escanee SOLO lo que el proyecto decide indexar.
        """
        try:
            from features.project.infrastructure.watcher import IgnoreRules
            root = self.repo_path or self.code_root
            return IgnoreRules(str(root)) if root else None
        except Exception:
            return None

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

    def _watcher_fingerprint(self) -> str:
        """Computa fingerprint del código usando el watcher (mismo algoritmo).

        Esto garantiza que el source_fingerprint almacenado en CPG sea
        compatible con el que compute el watcher en cada scan(), permitiendo
        verificar si CPG es espejo del código actual.
        """
        try:
            from features.project.infrastructure.watcher import FileWatcher
            root = self.repo_path or self.code_root
            return FileWatcher(root).compute_fingerprint()
        except Exception:
            return ""

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

    def _persist_quality_report(self, paths: list[str]) -> dict:
        """Corre code review (métricas + quality gates) y lo persiste como
        quality_report.json junto al CPG.

        El CPG siempre refleja el código (aunque tenga malas métricas), pero
        así el usuario/agente CONOCE el estado de calidad en el momento del
        index y en cada status posterior, sin consultar.

        Best-effort: si qlty no está disponible/configurado, intenta
        inicializarlo; si falla, guarda un reporte degradado explícito
        (nunca un falso "PASS" con métricas en 0).
        """
        # 0. Resolver repo real del código (el que el CPG refleja). Si el
        #    project.json legacy define location.repo_path, ProjectService lo
        #    resuelve; si no, usamos code_root directamente.
        project_root = self.code_root or self.repo_path or "."
        # Siempre pasar paths explícitos al review (nunca None → _resolve_default_paths
        # puede fallar si el repo no tiene project.json en la jerarquía).
        code_paths: list[str] = list(paths or []) if paths else self.resolve_code_paths()
        try:
            from features.project import ProjectService
            resolved = ProjectService.resolve_paths(self.repo_path or self.code_root)
            if resolved.project_root and os.path.isdir(resolved.project_root):
                project_root = resolved.project_root
        except Exception:
            pass

        # 1. Asegurar qlty config (best-effort: qlty init --yes en repo real)
        try:
            import subprocess as _sp
            qlty_root = project_root
            check = _sp.run(["qlty", "metrics", "--quiet", qlty_root],
                            capture_output=True, text=True, timeout=30)
            if check.returncode != 0 and "No qlty config" in check.stderr:
                _sp.run(["qlty", "init", "--yes"], cwd=qlty_root,
                        capture_output=True, text=True, timeout=60)
        except Exception:
            pass

        try:
            from features.code_analysis.application.review_service import ReviewUseCase
            from features.code_analysis.infrastructure.metrics_cache import IncrementalMetricsCache

            # Cache incremental de métricas qlty: se reutiliza entre builds,
            # evita re-ejecutar qlty sobre archivos no modificados.
            cache = IncrementalMetricsCache(project_root, data_dir=self.data_dir)

            svc = ReviewUseCase(project_root, project_id=self.project_id,
                                scan_paths=code_paths,
                                data_dir=self.data_dir,
                                cache=cache)
            result = svc.review_files(files=code_paths)
            report = result.to_dict()
            report["cache"] = cache.stats

            # Detectar falso PASS por qlty no disponible (metrics 0 reales)
            metrics = report.get("metrics", {}) or {}
            degraded = report.get("degraded", False)
            if not degraded and metrics.get("files", 0) == 0:
                report["degraded"] = True
                report["error"] = (
                    "qlty metrics no produjo archivos — probablemente qlty no "
                    "está configurado para este repo. Corre 'qlty init' o "
                    "indexa desde el status del proyecto."
                )
                report["summary"] = "⚠️ quality report degradado (qlty sin config)"
        except Exception as exc:
            report = {
                "passed": True,
                "degraded": True,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                "summary": "quality report no disponible (best-effort)",
                "gates": [],
                "metrics": {},
                "findings": {},
                "recommendations": [],
                "issues": [],
                "files_scanned": [],
            }

        import json as _json
        report["generated_at"] = _now()
        report["scope"] = "build"
        try:
            import os as _os
            _os.makedirs(self.data_dir, exist_ok=True)
            with open(_os.path.join(self.data_dir, "quality_report.json"), "w") as f:
                _json.dump(report, f, indent=2, ensure_ascii=False, default=str)
        except Exception:
            pass
        return report

    def _persist_bug_proposals(self) -> dict:
        """Análisis de bugs post-index vía CPG, guardado como bug_proposals.json.

        Se ejecuta DESPUÉS de indexar (el CPG ya es espejo del código). Detecta:
          - dead_branches / missing_returns / infinite_loops (analysis_results)
          - unused_functions (nodos huérfanos del grafo)
          - hardcoded_secrets / taint_paths (dfg)

        El reporte se persiste para que status lo muestre al usuario/agente sin
        que tengan que consultar el estado del código.
        """
        proposals: dict[str, list[dict]] = {
            "dead_branches": [],
            "missing_returns": [],
            "infinite_loops": [],
            "unused_functions": [],
            "hardcoded_secrets": [],
            "taint_paths": [],
        }
        try:
            data = self._repo.load(self.project_id)
            analysis = data.get("analysis_results", {}) or {}
            dfg = data.get("dfg", {}) or {}

            for key in ("dead_branches", "missing_returns", "infinite_loops"):
                for item in analysis.get(key, []) or []:
                    proposals[key].append({
                        "severity": "critical" if key == "infinite_loops" else "warning",
                        "label": key.replace("_", " "),
                        "file": item.get("file", ""),
                        "line": item.get("line", 0),
                        "detail": str(item.get("detail", ""))[:120],
                    })

            # unused_functions: nodos función sin CALLS incoming
            nodes = data.get("nodes", {})
            edges = data.get("edges", [])
            incoming_ids = {e.get("from") for e in edges if e.get("type") == "CALLS"}
            for nid, n in nodes.items():
                if not isinstance(n, dict):
                    continue
                if n.get("type") in ("function", "method") and not n.get("is_import"):
                    if nid not in incoming_ids and not n.get("name", "").startswith("__"):
                        proposals["unused_functions"].append({
                            "severity": "warning",
                            "label": f"función sin llamadas: {n.get('name')}",
                            "file": n.get("path", ""),
                            "line": n.get("line", 0),
                            "detail": f"{n.get('name')} no tiene callers en el CPG",
                        })

            for item in dfg.get("hardcoded_secrets", []) or []:
                proposals["hardcoded_secrets"].append({
                    "severity": "critical",
                    "label": "hardcoded secret",
                    "file": item.get("file", ""),
                    "line": item.get("line", 0),
                    "detail": str(item.get("detail", item.get("text", "")))[:120],
                })
            for item in dfg.get("taint_paths", []) or []:
                proposals["taint_paths"].append({
                    "severity": "high",
                    "label": "taint path",
                    "file": item.get("file", ""),
                    "line": item.get("line", 0),
                    "detail": str(item.get("detail", item.get("text", "")))[:120],
                })
        except Exception:
            pass

        findings = []
        for key, items in proposals.items():
            for it in items:
                it["type"] = key
                findings.append(it)

        payload = {
            "generated_at": _now(),
            "total_findings": len(findings),
            "by_type": {k: len(v) for k, v in proposals.items() if v},
            "findings": findings[:200],
        }
        try:
            import json as _json
            import os as _os
            _os.makedirs(self.data_dir, exist_ok=True)
            with open(_os.path.join(self.data_dir, "bug_proposals.json"), "w") as f:
                _json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
        except Exception:
            pass
        return payload


    def _build_business_layer(self, data: dict) -> dict:
        """Construye la capa de negocio sobre el grafo CPG.

        Deriva (sin re-escanear codigo):
          - pure_functions: funciones reutilizables sin side-effects
          - business_flows: cadenas CALLS completas (flujos de negocio)
          - architecture_patterns: TEA, hooks, redux, pure_domain
          - metrics: acoplamiento, funciones huerfanas, etc.
        """
        try:
            from features.code_analysis.application.business_analyzer import analyze
            return analyze(data.get("nodes", {}), data.get("edges", []))
        except Exception:
            return {
                "pure_functions": [],
                "business_flows": [],
                "architecture_patterns": [],
                "metrics": {},
            }

    def _sync_to_cognitive(self, data: dict) -> None:
        """Sincroniza nodos CPG a CognitiveGraph via batch store.

        Usa store_many (escritura UNICA) en vez de save() por nodo,
        que recargaba y reescribia el indice completo por cada nodo
        (O(n²) — hacia el build inviable con miles de nodos).
        """
        try:
            from libs.cognitive_graph import CognitiveGraph

            cg = CognitiveGraph(self.data_dir, auto_register=True)

            nodes = data.get("nodes", {})
            docs = {}
            for nid, node_data in nodes.items():
                docs[nid] = {
                    "name": node_data.get("name", ""),
                    "type": node_data.get("type", ""),
                    "file": node_data.get("path", ""),
                    "line": node_data.get("line", 0),
                }
            cg.store_many("cpg", docs)
        except Exception:
            pass  # sync best-effort

    def build(self, scan_paths: list[str] | None = None) -> dict:
        """Full rebuild via ast-grep + CFG analysis (CALLS edges).

        Automaticamente aplica auto-fix de lint antes de escanear.
        Al finalizar, sincroniza nodos CPG a CognitiveGraph via CodeNode entities.
        """
        paths = self.resolve_code_paths(scan_paths)

        # 0. Auto-fix antes de escanear (oculto, qlty es detalle interno)
        self._auto_fix(paths)

        # 1. ast-grep scan (multi-language) → nodos + imports
        from features.code_analysis.infrastructure.ast_grep_adapter import scan_files

        graph = scan_files(paths, ignore=self._ignore)

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
        data["source_fingerprint"] = self._watcher_fingerprint()
        self._repo.save(data)

        # 3. CFG scan → CALLS edges entre funciones
        self.scan_cfg(paths)

        # 4. Recargar con CALLS edges incluidos
        data = self._repo.load(self.project_id)

        # 4b. Capa de negocio: pure functions, flujos, patrones, metricas
        data["business_layer"] = self._build_business_layer(data)
        data["updated_at"] = _now()
        self._repo.save(data)

        # 4c. Quality report: métricas + gates persistidos junto al CPG
        data["quality_report"] = self._persist_quality_report(paths)

        # 4d. Post-index bug analysis: propuestas de bugs vía CPG (para status)
        data["bug_proposals"] = self._persist_bug_proposals()

        # 5. Sincronizar a CognitiveGraph via CodeNode entities
        self._sync_to_cognitive(data)

        return data

    def update(self, refresh: bool = False) -> dict:
        """Incremental = full rebuild (por ahora)."""
        return self.build()

    def index_task_files(self, task_files: list[str]) -> dict:
        """Re-indexa solo los archivos cambiados o eliminados.

        - Archivos existentes: re-scan con ast-grep (nuevos nodos + edges)
        - Archivos eliminados: remueve sus nodos viejos del CPG
        - Merge con el resto del CPG (sin re-scan completo)

        PRINCIPIO: escritura SEGURA. Primero se escanea y parsea TODO;
        si el scan falla o no produce nodos, NO se toca el CPG en disco.
        Esto evita dejar el CPG corrupto a medio merge.
        """
        if not task_files:
            return {"ok": True, "files_updated": 0, "scope": "task"}

        base = self.repo_path or self.code_root
        existing = []
        all_abs = []
        for f in task_files:
            p = os.path.join(base, f)
            all_abs.append(p)
            if os.path.exists(p):
                existing.append(p)

        # ── 1. Escanear SOLO los archivos que existen ──
        from features.code_analysis.infrastructure.ast_grep_adapter import scan_files

        if not existing:
            # Todos eliminados: solo remover (no hay scan que fallar)
            new_nodes: dict = {}
            new_edges: list = []
        else:
            new_graph = scan_files(existing, ignore=self._ignore)
            new_nodes = {nid: n.to_dict() for nid, n in new_graph.nodes.items()}
            new_edges = [e.to_dict() for e in new_graph.edges]
            if not new_nodes:
                return {
                    "ok": False,
                    "files_updated": 0,
                    "scope": "task",
                    "error": "scan_files no produjo nodos — CPG NO modificado",
                }

        # ── 2. Solo AHORA remover viejos y mergear ──
        old_data = self._repo.load(self.project_id)
        old_nodes = old_data.get("nodes", {})
        old_edges = old_data.get("edges", [])

        changed_paths = set(all_abs)
        paths_to_remove = {
            nid
            for nid, n in old_nodes.items()
            if isinstance(n, dict) and n.get("path") in changed_paths
        }

        data = dict(old_data)
        data["nodes"] = {
            nid: n for nid, n in old_nodes.items() if nid not in paths_to_remove
        }
        data["nodes"].update(new_nodes)
        data["edges"] = [
            e
            for e in old_edges
            if e["from"] not in paths_to_remove and e["to"] not in paths_to_remove
        ] + new_edges
        data["updated_at"] = _now()
        data["update_count"] = data.get("update_count", 0) + 1
        # Recompute fingerprint (watcher-compatible) over all source paths after merge
        data["source_fingerprint"] = self._watcher_fingerprint()

        self._repo.save(data)
        # Sync a CognitiveGraph via CodeNode entities
        self._sync_to_cognitive(data)
        # Quality report sobre los archivos re-indexados
        qr = self._persist_quality_report(existing)
        # Post-index bug proposals (para status)
        bp = self._persist_bug_proposals()
        return {
            "ok": True,
            "files_updated": len(existing),
            "nodes_added": len(new_nodes),
            "nodes_removed": len(paths_to_remove),
            "scope": "task",
            "quality_report": qr,
            "bug_proposals": bp,
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

        # 3b. Índices O(1) para el matching de calls (evita scans lineales
        #     de cpg_nodes por cada call → cuello de botella en repos grandes).
        nodes_by_name_path: dict[tuple[str, str], str] = {}
        nodes_by_name: dict[str, list[str]] = {}
        for nid, n in cpg_nodes.items():
            if not isinstance(n, dict):
                continue
            if n.get("type") in ("function", "method") and not n.get("is_import"):
                name = n.get("name", "")
                path = n.get("path", "")
                if name:
                    nodes_by_name_path[(name, path)] = nid
                    nodes_by_name.setdefault(name, []).append(nid)

        edge_set: set[tuple[str, str, str]] = {
            (e.get("from"), e.get("to"), e.get("type"))
            for e in cpg_edges
            if isinstance(e, dict)
        }

        # 4. Detectar llamadas multi-lenguaje via ast-grep.
        #    Con caché incremental por mtime: solo re-ejecuta ast-grep sobre
        #    archivos nuevos/modificados (evita N subprocess en cada build).
        from features.code_analysis.infrastructure.ast_grep_calls import (
            detect_calls_cached,
            CallsCache,
        )

        import os as _os
        cfg_cache = CallsCache(_os.path.join(self.data_dir, "cfg_calls_cache.json"))

        total_calls = 0
        cache_hits = 0
        for fp in all_files:
            if not os.path.isfile(fp):
                continue
            definitions = defs_by_file.get(fp, [])
            if not definitions:
                continue  # Skip files without known function definitions

            try:
                calls, from_cache = detect_calls_cached(fp, definitions, cfg_cache)
                if from_cache:
                    cache_hits += 1
            except Exception:
                continue

            for call in calls:
                caller = call["caller"]
                callee = call["callee"]
                call_line = call["line"]

                if not caller or not callee or caller.startswith("$"):
                    continue

                total_calls += 1

                # Caller node: lookup O(1) por (name, path)
                caller_nid = nodes_by_name_path.get((caller, fp))
                if not caller_nid:
                    caller_nid = _cpg_nid()
                    cpg_nodes[caller_nid] = {
                        "id": caller_nid,
                        "type": "function",
                        "name": caller,
                        "path": fp,
                        "line": call_line,
                    }
                    nodes_by_name_path[(caller, fp)] = caller_nid
                    nodes_by_name.setdefault(caller, []).append(caller_nid)

                # Callee node: buscar por nombre (cualquier archivo) O(1)
                callee_nid = None
                cands = nodes_by_name.get(callee, [])
                if cands:
                    callee_nid = cands[0]
                else:
                    simple_name = callee.split(".")[-1] if "." in callee else callee
                    if simple_name != callee:
                        cands = nodes_by_name.get(simple_name, [])
                        if cands:
                            callee_nid = cands[0]
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
                    nodes_by_name.setdefault(callee, []).append(callee_nid)

                # Edge (dedup O(1) via edge_set)
                edge_key = (caller_nid, callee_nid, "CALLS")
                if edge_key not in edge_set:
                    edge_set.add(edge_key)
                    cpg_edges.append(
                        {"from": caller_nid, "to": callee_nid, "type": "CALLS"}
                    )

        self._repo.save(data)
        cfg_cache.save()
        # Quality report tras CFG scan (CALLS edges disponibles para review)
        self._persist_quality_report(all_files)
        result = data.get(
            "analysis_results",
            {
                "dead_branches": [],
                "missing_returns": [],
                "infinite_loops": [],
            },
        )
        if isinstance(result, dict):
            result["cfg_cache"] = cfg_cache.stats
            result["cfg_cache_hits"] = cache_hits
        return result

    def scan_dfg(self, scan_paths: list[str] | None = None, verbose: bool = False) -> dict:
        """Security analysis via ast-grep patterns (multi-lenguaje).

        Busca hardcoded secrets, taint paths, y patrones de seguridad
        usando ast-grep deep patterns de tools/codereview/config.py.

        Optimizaciones:
          - Caché incremental por archivo (mtime/size) → solo re-ejecuta
            ast-grep sobre archivos modificados (evita ~29 subprocess × archivo).
          - Progreso a stderr (verbose) para no bloquear sin feedback.
        """
        from features.code_analysis.infrastructure.ast_grep_adapter import _run_ast_grep, _detect_lang
        from features.code_analysis.infrastructure.ast_grep_calls import CallsCache

        def _eprint(*a):
            if verbose:
                print(*a, file=sys.stderr, flush=True)

        raw_paths = scan_paths if scan_paths is not None else self.resolve_code_paths()
        all_files = []
        _data_dir_abs = os.path.realpath(self.data_dir)
        _cache_markers = ("_cache.json", "quality_report.json", "bug_proposals.json")
        for p in raw_paths:
            if os.path.isfile(p):
                all_files.append(p)
            elif os.path.isdir(p):
                for root, dirs, files in os.walk(p):
                    dirs[:] = [
                        d for d in dirs if not d.startswith(".") and d != "__pycache__"
                    ]
                    for f in files:
                        full = os.path.join(root, f)
                        # Excluir el data_dir (cachés/JSONs generados) y archivos de cache
                        if os.path.realpath(full).startswith(_data_dir_abs):
                            continue
                        if f.endswith(_cache_markers) or f == "qlty_metrics_cache.json":
                            continue
                        all_files.append(full)

        # Limitar por performance, pero reportar el límite
        MAX_FILES = 200
        scan_files = all_files[:MAX_FILES]
        if len(all_files) > MAX_FILES and verbose:
            _eprint(f"  ⚠️  DFG: {len(all_files)} archivos, analizando los {MAX_FILES} primeros")

        all_results = {
            "hardcoded_secrets": [],
            "taint_paths": [],
            "uninitialized_vars": [],
            "data_gaps": [],
        }

        # Caché incremental de resultados por archivo
        import sys
        dfg_cache = CallsCache(os.path.join(self.data_dir, "dfg_cache.json"))
        cache_hits = 0

        try:
            from features.code_analysis.infrastructure.codereview.config import _DEEP_PATTERNS

            # Usar patterns de seguridad y producción
            security_patterns = _DEEP_PATTERNS.get("danger", []) + _DEEP_PATTERNS.get(
                "production-bugs", []
            )

            total = len(scan_files)
            for idx, fp in enumerate(scan_files, 1):
                if not os.path.isfile(fp):
                    continue
                lang = _detect_lang(fp)
                if not lang:
                    continue

                # 1. Caché: si el archivo no cambió, reusar hallazgos previos
                cached = dfg_cache.get(fp)
                if cached is not None:
                    cache_hits += 1
                    all_results["hardcoded_secrets"].extend(cached.get("hardcoded_secrets", []))
                    all_results["taint_paths"].extend(cached.get("taint_paths", []))
                    all_results["uninitialized_vars"].extend(cached.get("uninitialized_vars", []))
                    all_results["data_gaps"].extend(cached.get("data_gaps", []))
                    if verbose and total > 20 and idx % 20 == 0:
                        _eprint(f"  🔍 DFG [{idx}/{total}] (cache) {os.path.basename(fp)}")
                    continue

                # 2. Correr patrones sobre este archivo
                file_findings = {
                    "hardcoded_secrets": [],
                    "taint_paths": [],
                    "uninitialized_vars": [],
                    "data_gaps": [],
                }
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
                            file_findings["hardcoded_secrets"].append(entry)
                        else:
                            file_findings["taint_paths"].append(entry)

                # Guardar en cache
                dfg_cache.set(fp, file_findings)

                # Acumular
                for k in file_findings:
                    all_results[k].extend(file_findings[k])

                if verbose and (total <= 20 or idx % 20 == 0):
                    _eprint(f"  🔍 DFG [{idx}/{total}] {os.path.basename(fp)}")
        except Exception:
            pass
        finally:
            dfg_cache.save()

        data = self._repo.load(self.project_id)
        data["dfg"] = all_results
        data["dfg"]["_stats"] = {
            "files_scanned": len(scan_files),
            "cache_hits": cache_hits,
            "cache": dfg_cache.stats,
        }
        self._repo.save(data)
        # Quality report tras DFG (security findings disponibles)
        self._persist_quality_report(all_files)
        if verbose:
            _eprint(
                f"  ✅ DFG completo: {len(all_results['hardcoded_secrets'])} secrets, "
                f"{len(all_results['taint_paths'])} taint, {cache_hits} cache hits"
            )
        return all_results

    def scan_dfg_async(self, scan_paths: list[str] | None = None) -> dict:
        """Ejecuta scan_dfg en background y persiste un estado consultable.

        No bloquea al agente: lanza un thread que corre el scan y escribe
        data_dir/dfg_scan_status.json con:
          - status: running → ok | failed
          - started_at / finished_at
          - summary: resumen de hallazgos
          - error: mensaje si falla

        El agente/usuario consulta el estado cuando quiera (status del walker,
        o leyendo el archivo) sin esperar 5 minutos sin feedback.
        """
        import threading

        status_path = os.path.join(self.data_dir, "dfg_scan_status.json")

        def _write_status(payload: dict) -> None:
            try:
                import json as _json
                os.makedirs(self.data_dir, exist_ok=True)
                with open(status_path, "w") as f:
                    _json.dump(payload, f, indent=2, ensure_ascii=False)
            except OSError:
                pass

        _write_status({
            "status": "running",
            "started_at": _now(),
            "finished_at": "",
            "summary": "",
            "error": "",
        })

        def _worker() -> None:
            try:
                result = self.scan_dfg(scan_paths, verbose=False)
                _write_status({
                    "status": "ok",
                    "started_at": status_started,
                    "finished_at": _now(),
                    "summary": (
                        f"{len(result.get('hardcoded_secrets', []))} secrets, "
                        f"{len(result.get('taint_paths', []))} taint, "
                        f"{len(result.get('uninitialized_vars', []))} uninit vars, "
                        f"{len(result.get('data_gaps', []))} data gaps"
                    ),
                    "error": "",
                    "files_scanned": (result.get("_stats") or {}).get("files_scanned", 0),
                })
            except Exception as e:
                _write_status({
                    "status": "failed",
                    "started_at": status_started,
                    "finished_at": _now(),
                    "summary": "",
                    "error": f"{type(e).__name__}: {str(e)[:200]}",
                })

        status_started = _now()
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return {"ok": True, "status": "running", "status_path": status_path}

    def dfg_scan_status(self) -> dict:
        """Lee el estado del scan_dfg asíncrono (si existe)."""
        status_path = os.path.join(self.data_dir, "dfg_scan_status.json")
        try:
            import json as _json
            with open(status_path) as f:
                return _json.load(f)
        except Exception:
            return {"status": "not_started"}
