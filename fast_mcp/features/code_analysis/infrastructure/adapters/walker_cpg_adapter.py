"""code_analysis/infrastructure/adapters/walker_cpg_adapter.py — Adapter hacia task_execution.

Implementa ICpgAnalysisPort (puerto definido en task_execution) orquestando
CognitiveGraph + servicios de dominio de code_analysis.

SRP: este archivo es el ÚNICO punto de acoplamiento entre code_analysis
y task_execution. Si task_execution cambia el puerto, solo este adapter
se actualiza.
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import tempfile
from typing import Any

from fast_mcp.features.code_analysis.application.ports.cpg_repository import CpgRepository
from fast_mcp.features.code_analysis.domain.events import AnalysisCompleted, EventBus
from fast_mcp.features.code_analysis.domain.services.analysis_service import IBusinessAnalyzer
from fast_mcp.shared_kernel.ports import ICpgAnalysisPort


class WalkerCpgAnalysisAdapter(ICpgAnalysisPort):
    """Implementación del puerto de task_execution sobre code_analysis.

    Nest-like (ADR-052/053): el adapter SOLO traduce el puerto (repo +
    eventos); la orquestación de la lógica vive en el Application Service
    (CodeAnalysisService) inyectado. build/scan/index son operaciones de
    mantenimiento que se mantienen acá (vía BuildGraphUseCase).
    """

    def __init__(
        self,
        repo: CpgRepository,
        analyzer: IBusinessAnalyzer | None = None,
        event_bus: EventBus | None = None,
        service: Any = None,
        ckg: Any = None,
    ):
        self._repo = repo
        self._analyzer = analyzer
        self._bus = event_bus
        self._ckg = ckg
        # Application Service inyectado (DI). Default defensivo: construido
        # desde el data_dir del repo (mismo resultado que el provider DI).
        if service is None:
            from fast_mcp.features.code_analysis.application.services.code_analysis_service import (
                CodeAnalysisService,
            )

            data_dir = getattr(repo, "data_dir", None)
            if data_dir:
                service = CodeAnalysisService(data_dir)
            else:
                service = CodeAnalysisService()
        self._service = service

    def business_layer(self, focus: str = "") -> dict[str, object]:
        result = self._service.business_layer(focus=focus)
        self._emit(
            AnalysisCompleted(
                analysis_type="business_layer",
                scope=focus or "project",
                result_summary={
                    "pure_functions": len(result.get("pure_functions", [])),
                    "flows": len(result.get("business_flows", [])),
                    "patterns": len(result.get("architecture_patterns", [])),
                },
            )
        )
        return result

    def detect_unused_functions(self, file_filter: str = "") -> dict[str, object]:
        result = self._service.detect_unused_functions(file_filter=file_filter)
        self._emit(
            AnalysisCompleted(
                analysis_type="dead_code",
                scope=file_filter or "project",
                result_summary={"total": result.get("total", 0)},
            )
        )
        return result

    def gather_context(self, files: list[str] | None = None) -> dict[str, object]:
        return self._service.gather_context(files=files)

    def business_layer(self, focus: str = "") -> dict[str, object]:
        result = self._service.business_layer(focus=focus)
        self._emit(
            AnalysisCompleted(
                analysis_type="business_layer",
                scope=focus or "project",
                result_summary={
                    "pure_functions": len(result.get("pure_functions", [])),
                    "flows": len(result.get("business_flows", [])),
                    "patterns": len(result.get("architecture_patterns", [])),
                },
            )
        )
        return result

    def detect_unused_functions(self, file_filter: str = "") -> dict[str, object]:
        result = self._service.detect_unused_functions(file_filter=file_filter)
        self._emit(
            AnalysisCompleted(
                analysis_type="dead_code",
                scope=file_filter or "project",
                result_summary={"total": result.get("total", 0)},
            )
        )
        return result

    def review_code(
        self, files: list[str] | None = None, thresholds: dict | None = None
    ) -> dict[str, object]:
        result_dict = self._service.review_code(files=files, thresholds=thresholds)
        self._emit(
            AnalysisCompleted(
                analysis_type="code_review",
                scope=",".join(files) if files else "project",
                result_summary={
                    "passed": result_dict.get("passed"),
                    "files": len(result_dict.get("files_scanned", [])),
                    "gates": len(result_dict.get("gates", [])),
                },
            )
        )
        return result_dict

    def project_metrics(self, file_filter: str = "") -> dict[str, object]:
        """Métricas del proyecto (delegadas al Application Service).

        ADR-WALKER-053: CPG-nativas, sin binario externo.
        """
        result_dict = self._service.project_metrics(file_filter=file_filter)
        self._emit(
            AnalysisCompleted(
                analysis_type="project_metrics",
                scope=file_filter or "project",
                result_summary=result_dict.get("metrics", {})
                if result_dict.get("metrics")
                else {"error": result_dict.get("error", "empty")},
            )
        )
        return result_dict

    def find_bugs(self, bug_type: str = "taint_paths", file_filter: str = "") -> dict[str, object]:
        """Detección de bugs vía CFG/DFG (delegada en CodeAnalysisService)."""
        result = self._service.find_bugs(bug_type=bug_type, file_filter=file_filter)
        self._emit(
            AnalysisCompleted(
                analysis_type="bug_scan",
                scope=file_filter or "project",
                result_summary={"total": result.get("total", 0), "bug": bug_type},
            )
        )
        return result

    def security_scan(self, vuln_type: str = "all", file_filter: str = "") -> dict[str, object]:
        """Security scan vía taint analysis DFG (delegada en CodeAnalysisService)."""
        result = self._service.security_scan(vuln_type=vuln_type, file_filter=file_filter)
        self._emit(
            AnalysisCompleted(
                analysis_type="security_scan",
                scope=file_filter or "project",
                result_summary={"total": result.get("total", 0), "vuln": vuln_type},
            )
        )
        return result

    def dataflow(self, file: str = "", symbol: str = "") -> dict[str, object]:
        """Data-flow de un símbolo desde dfg.json (delegada en CodeAnalysisService)."""
        return self._service.dataflow(file=file, symbol=symbol)

    def controlflow(self, file: str = "", symbol: str = "") -> dict[str, object]:
        """Control-flow findings desde analysis_results.json (delegada en CodeAnalysisService)."""
        return self._service.controlflow(file=file, symbol=symbol)

    def query(self, query_text: str = "") -> dict[str, object]:
        """DSL query sobre el CPG (delegada en CodeAnalysisService.grep)."""
        return self._service.query(query=query_text)

    def detect_imports(self, files: list[str] | None = None, project_root: str = "") -> dict[str, object]:
        """Detecta imports alucinados en archivos del proyecto (hallucination.py).

        W6 (ADR-076): consume `infrastructure/hallucination.py` (HU-3) para
        verificar que cada import resuelva a un símbolo real del proyecto,
        stdlib o terceros. Fail-fast si no hay contexto de proyecto.
        """
        from fast_mcp.features.code_analysis.infrastructure.hallucination import scan_file
        from pathlib import Path

        if not files:
            return {"files_scanned": 0, "hallucinations": 0, "issues": []}

        root = project_root or getattr(self._service, "_chain_dir", "") or ""
        if not root:
            return {
                "error": "project_root no resuelto. Inyecta project_root o usa chain_dir canónico.",
                "files_scanned": 0,
                "hallucinations": 0,
                "issues": [],
            }

        issues: list[dict] = []
        hallucinations = 0
        for file_path in files:
            result = scan_file(Path(file_path), root)
            file_issues = result.get("issues", [])
            issues.extend(file_issues)
            hallucinations += result.get("hallucinations", 0)

        return {
            "files_scanned": len(files),
            "hallucinations": hallucinations,
            "issues": issues[:50],
            "truncated": len(issues) > 50,
        }

    # Alias para compatibilidad con el helper de task handlers (action=imports).
    imports = detect_imports

    def check_conventions(self, languages: list[str] | None = None) -> dict[str, object]:
        """Chequea convenciones del CKG contra el código (delegada en CodeAnalysisService).

        W7 (ADR-076): consume CKG (self._ckg) inyectado en el adapter.
        Si no hay CKG disponible, retorna error con remediación explícita
        (fail-fast, nunca [] silencioso).
        """
        if self._ckg is None:
            return {
                "error": "CKG no inyectado en el adapter. "
                "Inyecta CKG al construir WalkerCpgAnalysisAdapter "
                "(composition root / cpg_analysis_adapter).",
                "rules_checked": 0,
                "violations": [],
                "by_rule": {},
            }
        report = self._service.check_conventions(languages=languages, ckg=self._ckg)
        if isinstance(report, dict):
            report.setdefault("rules_checked", 0)
            report.setdefault("violations", [])
            report.setdefault("by_rule", {})
        return report

    # Alias para compatibilidad con el helper de task handlers (action=conventions).
    conventions = check_conventions

    def scan_dfg(
        self, action: str = "start", project_root: str = ""
    ) -> dict[str, object]:
        from fast_mcp.features.code_analysis import CPGBuilder

        builder = CPGBuilder(project_root)
        if action == "status":
            status = builder.dfg_scan_status()
            status["action"] = "status"
            return status

        result = builder.scan_dfg_async([project_root])
        self._emit(
            AnalysisCompleted(
                analysis_type="dfg_scan",
                scope=project_root,
                result_summary={"status": result.get("status")},
            )
        )
        return {
            "action": "start",
            "status": result.get("status"),
            "status_path": result.get("status_path"),
            "next_step": "Re-ejecuta con action='status' para consultar el resultado",
        }

    def build_cpg(
        self, project_root: str, scan_paths: list[str] | None = None
    ) -> dict[str, object]:
        from fast_mcp.features.code_analysis import CPGBuilder

        builder = CPGBuilder(project_root)
        result = builder.build(scan_paths)
        stats = result.get("stats", result)
        result_dict = {
            "built": True,
            "nodes": stats.get("total_nodes", len(result.get("nodes", {}))),
            "edges": stats.get("total_edges", len(result.get("edges", []))),
        }
        self._emit(
            AnalysisCompleted(
                analysis_type="cpg_build",
                scope=project_root,
                result_summary=result_dict,
            )
        )
        return result_dict

    def update(
        self, refresh: bool = False, project_root: str = ""
    ) -> dict[str, object]:
        """Sync incremental del CPG via watcher (delega en CPGBuilder.update).

        FIX (rebuild innecesario): el adapter no exponia update(), por lo que
        toda peticion incremental degradaba silenciosamente a build_cpg()
        (rescan completo). Ahora el camino rapido es real.

        Returns resumen ligero (mismo shape que build_cpg) para no arrastrar
        el documento CPG completo por el CLI/MCP.
        """
        if not project_root:
            # Sin contexto no hay fingerprint contra quien comparar:
            # rebuild completo es la unica opcion segura.
            raise ValueError(
                "update() requiere project_root; usa build_cpg si no lo tienes"
            )

        from fast_mcp.features.code_analysis import CPGBuilder

        builder = CPGBuilder(project_root)
        result = builder.update(refresh=refresh)
        stats = result.get("stats", result)
        result_dict: dict[str, object] = {
            "built": bool(refresh),
            "mode": "full" if refresh else "incremental",
            "nodes": stats.get("total_nodes", len(result.get("nodes", {}))),
            "edges": stats.get("total_edges", len(result.get("edges", []))),
            "files_updated": result.get("files_updated", 0),
        }
        warnings = result.get("warnings")
        if warnings:
            result_dict["warnings"] = warnings
        self._emit(
            AnalysisCompleted(
                analysis_type="cpg_update" if not refresh else "cpg_build",
                scope=project_root,
                result_summary={"mode": result_dict["mode"], "files_updated": result_dict["files_updated"]},
            )
        )
        return result_dict

    def index_ref(
        self,
        url: str,
        name: str,
        branch: str = "main",
        depth: int = 1,
        data_dir: str = "",
    ) -> dict[str, object]:
        from fast_mcp.features.code_analysis.infrastructure.ast_grep_adapter import (
            run_outline,
            parse_outline,
        )

        out_path = os.path.join(data_dir, f"cpg.ref.{name}.json.gz")
        tmpdir = None
        try:
            tmpdir = tempfile.mkdtemp(prefix=f"cpg-ref-{name}-")
            repo_path = os.path.join(tmpdir, name)

            branches_to_try = [branch] if branch != "main" else ["main", "master"]
            cloned = False
            for b in branches_to_try:
                subprocess.run(
                    [
                        "git",
                        "clone",
                        "--depth",
                        str(depth),
                        "--branch",
                        b,
                        "--single-branch",
                        url,
                        repo_path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if os.path.isdir(repo_path):
                    branch = b
                    cloned = True
                    break
            if not cloned:
                return {"ok": False, "error": f"Failed to clone {url}"}

            results = run_outline([repo_path])
            if not results:
                return {"message": "No se encontraron archivos parseables", "url": url}

            graph = parse_outline(results)
            data_out = graph.to_dict()
            stats = {
                "version": "2.0",
                "project_id": f"ref:{name}",
                "generated_by": f"cpg_index_ref/{url}",
                "source_url": url,
                "source_branch": branch,
                "total_nodes": len(graph.nodes),
                "total_edges": len(graph.edges),
            }
            data_out.update(stats)

            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            payload = json.dumps(data_out, ensure_ascii=False, separators=(",", ":"))
            with open(out_path, "wb") as f:
                f.write(gzip.compress(payload.encode()))

            types = {}
            for n in graph.nodes.values():
                types[n.type] = types.get(n.type, 0) + 1

            result = {
                "url": url,
                "name": name,
                "index": f"cpg-ref-{name}",
                "path": out_path,
                "nodes": len(graph.nodes),
                "edges": len(graph.edges),
                "structure": types,
            }
            self._emit(
                AnalysisCompleted(
                    analysis_type="cpg_index_ref",
                    scope=name,
                    result_summary=result,
                )
            )
            return result
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"Timeout clonando {url}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            if tmpdir:
                shutil.rmtree(tmpdir, ignore_errors=True)

    def _emit(self, event: AnalysisCompleted) -> None:
        if self._bus is not None:
            try:
                self._bus.publish(event)
            except Exception:
                pass
