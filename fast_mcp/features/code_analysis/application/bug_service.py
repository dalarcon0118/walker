"""application/bug_service.py — ScanBugsUseCase + ScanSecurityUseCase.

Bug detection and security scanning using CPG + CFG/DFG analysis.

Result pattern: los cores puros con @safe devuelven Result (nunca lanzan);
los wrappers deciden con is_successful y hacen fallback explícito con log
(convention-001: nunca except silencioso).
"""

from __future__ import annotations
import logging
import os

from returns.pipeline import is_successful
from returns.result import safe

from fast_mcp.features.code_analysis.domain.graph import Graph, Node
from fast_mcp.features.code_analysis.domain.query import Query, Filter
from fast_mcp.features.code_analysis.application.ports.cpg_repository import CpgRepository
from fast_mcp.features.code_analysis.infrastructure.filters import match_file_filter

logger = logging.getLogger(__name__)


class ScanBugsUseCase:
    """Detect bugs and security issues in the CPG."""

    BUG_TYPES = {
        "dead_branches",
        "missing_returns",
        "infinite_loops",
        "uninitialized_vars",
        "taint_paths",
        "data_gaps",
        "unused_functions",
    }

    RISK_MAP = {
        "sqli": "SQL Injection",
        "command_injection": "Command Injection",
        "xss": "XSS",
        "code_injection": "Code Injection",
        "data_leak": "Data Leak",
        "weak_crypto": "Weak crypto",
        "path_traversal": "Path traversal",
        "hardcoded_secrets": "Hardcoded secret",
        "spoofing": "Spoofing",
        "all": None,
    }

    def __init__(self, chain_dir: str, repo: CpgRepository, graph: Graph):
        self.chain_dir = chain_dir
        self._repo = repo
        self._graph = graph

    @property
    def graph(self) -> Graph:
        return self._graph

    @property
    def nodes(self) -> dict[str, Node]:
        return self.graph.nodes

    @property
    def edges(self) -> list[dict]:
        return self.graph.edges

    def _load_repo_data(self) -> dict:
        """Carga el CPG persistido con fallback explícito.

        Result pattern: el core @safe nunca lanza; ante CPG corrupto
        devuelve {} y loggea el error (convention-001).
        """
        result = _load_repo_data_core(self._repo)
        if is_successful(result):
            return result.value_or({})
        error = result.failure()
        logger.error(
            "Carga de CPG para bugs/security fallo (%s: %s); sin findings",
            type(error).__name__, error,
        )
        return {}

    def _load_projection(self, name: str) -> dict:
        """Carga una proyección (analysis_results.json / dfg.json, F3b/R4).

        R4 (ADR-WALKER-055): delega en el ProjectionStore compartido.
        """
        from fast_mcp.shared_kernel.projection_store import ProjectionStore

        return ProjectionStore(self._repo.data_dir).read(name)

    # ── Bugs ──────────────────────────────────────────────────────────

    def handle_bugs(self, query: Query) -> dict | None:
        target = query.get("bug")
        if target and target not in self.BUG_TYPES:
            # Podria ser free text
            if query.free_text in self.BUG_TYPES:
                target = query.free_text
            else:
                return None
        if not target:
            return None

        attr_filters = query.exclude_keys({"bug"})
        bug_results = []

        if target in ("dead_branches", "missing_returns", "infinite_loops"):
            data = self._load_repo_data()
            cfg_data = (
                self._load_projection("analysis_results.json")
                or data.get("analysis_results", {})
            )
            if not cfg_data:
                cfg_data = self._scan_cfg_on_demand()
            cfg_map = {
                "dead_branches": "dead_branches",
                "missing_returns": "missing_returns",
                "infinite_loops": "infinite_loops",
            }
            if target in cfg_map and cfg_data:
                for item in cfg_data.get(cfg_map[target], []):
                    f = item.get("file", "")
                    if match_file_filter(f, attr_filters):
                        bug_results.append(
                            {
                                "bug": target,
                                "file": f,
                                "line": item.get("line", 0),
                                "detail": item.get("detail", ""),
                                "function": item.get("function", item.get("name", "")),
                            }
                        )

        elif target in ("uninitialized_vars", "taint_paths", "data_gaps"):
            data = self._load_repo_data()
            dfg_data = (
                self._load_projection("dfg.json")
                or data.get("dfg", {})
            )
            if not dfg_data:
                dfg_data = self._scan_dfg_on_demand()
            dfg_map = {
                "uninitialized_vars": "uninitialized_vars",
                "taint_paths": "taint_paths",
                "data_gaps": "data_gaps",
            }
            if target in dfg_map and dfg_data:
                for item in dfg_data.get(dfg_map[target], []):
                    f = item.get("file", "")
                    if isinstance(f, str) and match_file_filter(f, attr_filters):
                        bug_results.append(
                            {
                                "bug": target,
                                "file": f,
                                "line": item.get("line", 0),
                                "detail": item.get("detail", ""),
                                "variable": item.get("variable", item.get("path", "")),
                            }
                        )

        elif target == "unused_functions":
            bug_results = self._find_unused_functions(attr_filters)

        if not bug_results:
            return None
        return {
            "query": f"bug:{target}",
            "total": len(bug_results),
            "matches": bug_results[:50],
            "truncated": len(bug_results) > 50,
        }

    def _find_unused_functions(self, attr_filters: list[Filter]) -> list[dict]:
        """Find functions with no incoming CALLS edges.

        Filtra falsos positivos estructurales: fields de dataclass/ODM,
        constantes de clase, dunders de framework y funciones test_*
        (el CPG no las considera "usadas" porque se acceden por atributo,
        por naming de pytest o por imports lazy).
        """
        from fast_mcp.features.code_analysis.domain.analysis import (
            DeadCodeCandidate,
            DeadCodeReferenceScanner,
            is_false_positive_dead_code,
        )

        entry_points = {"main", "setup", "teardown", "__init__", "__main__"}
        results = []

        # Scanner de referencias por texto: detecta usos que el CPG no
        # conecta (registry dinámico, imports lazy, dispatch por nombre).
        scanner = DeadCodeReferenceScanner(
            sorted({n.path for n in self.graph.nodes.values() if n.path})
        )

        for nid, node in self.graph.nodes.items():
            if node.type not in ("function", "method"):
                continue
            if node.is_import:
                continue  # Skip imported symbols, only real definitions
            name = node.name
            if name in entry_points:
                continue
            if is_false_positive_dead_code(name, node.type, node.signature, node.path):
                continue

            incoming_calls = [
                ie for ie in self.graph.get_incoming(nid) if ie["type"] == "CALLS"
            ]
            if incoming_calls:
                continue
            if scanner.is_referenced(name, node.path, node.line):
                continue  # Referenciado por texto en otro archivo → vivo

            fpath = node.path
            if not match_file_filter(fpath, attr_filters):
                continue

            parent_class = ""
            if node.type == "method":
                for ie in self.graph.get_incoming(nid):
                    if ie["type"] == "CONTAINS":
                        parent = self.graph.get_node(ie["from"])
                        if parent and parent.type == "class":
                            parent_class = parent.name
                            break

            candidate = DeadCodeCandidate(
                function=name,
                file=fpath,
                line=node.line,
                is_private=name.startswith("_"),
                is_method=node.type == "method",
                incoming_calls=len(incoming_calls),
                incoming_imports=len(
                    [
                        ie
                        for ie in self.graph.get_incoming(nid)
                        if ie["type"] == "IMPORTS"
                    ]
                ),
                parent_class=parent_class,
                parent_type=node.type,
            )
            results.append(candidate.to_dict())

        return results

    def _scan_cfg_on_demand(self) -> dict:
        """Trigger CFG scan if not already present.

        Result pattern: el core @safe nunca lanza; ante fallo devuelve {}
        y loggea el error (convention-001: nunca except silencioso).
        """
        result = _scan_cfg_on_demand_core(self.chain_dir)
        if is_successful(result):
            return result.value_or({})
        error = result.failure()
        logger.error(
            "scan_cfg on-demand fallo (%s: %s); sin analysis_results",
            type(error).__name__, error,
        )
        return {}

    def _scan_dfg_on_demand(self) -> dict:
        """Corre scan_dfg on-demand si dfg no está proyectado (F3b).

        Result pattern: el core @safe nunca lanza; ante fallo devuelve {}
        y loggea el error (convention-001: nunca except silencioso).
        """
        result = _scan_dfg_on_demand_core(self.chain_dir)
        if is_successful(result):
            return result.value_or({})
        error = result.failure()
        logger.error(
            "scan_dfg on-demand fallo (%s: %s); sin dfg",
            type(error).__name__, error,
        )
        return {}

    # ── Security ─────────────────────────────────────────────────────

    def handle_security(self, query: Query) -> dict | None:
        target = query.get("security")
        if not target:
            return None

        attr_filters = query.exclude_keys({"security"})

        data = self._load_repo_data()
        dfg_data = self._load_projection("dfg.json") or data.get("dfg", {})
        if not dfg_data:
            dfg_data = self._scan_dfg_on_demand()
        taint_paths = dfg_data.get("taint_paths", [])
        hardcoded = dfg_data.get("hardcoded_secrets", [])
        security_findings = []

        target_risk = self.RISK_MAP.get(target)

        if target in ("hardcoded_secrets", "all"):
            for hs in hardcoded:
                f = hs.get("file", "")
                if match_file_filter(f, attr_filters):
                    security_findings.append(
                        {
                            "type": "hardcoded_secret",
                            "risk": "Hardcoded secret",
                            "file": str(f),
                            "line": hs.get("line", 0),
                            "variable": hs.get("variable", ""),
                            "detail": hs.get("detail", ""),
                        }
                    )

        for tp in taint_paths:
            if target_risk and tp.get("risk") != target_risk:
                continue
            f = tp.get("file", "")
            if match_file_filter(f, attr_filters):
                security_findings.append(
                    {
                        "type": "taint_flow",
                        "flow": tp.get("path", ""),
                        "risk": tp.get("risk", "?"),
                        "file": str(f),
                        "line": tp.get("line", 0),
                        "detail": tp.get("detail", ""),
                    }
                )

        return {
            "query": f"security:{target}",
            "security_scan": target,
            "taint_flows": security_findings[:50],
            "taint_total": len(security_findings),
            "truncated": len(security_findings) > 50,
        }


# ── Cores puros (@safe) ─────────────────────────────────────────

@safe
def _load_repo_data_core(repo: CpgRepository) -> dict:
    """Core puro de carga de datos CPG (puede lanzar)."""
    return repo.load() or {}


@safe
def _scan_cfg_on_demand_core(chain_dir: str) -> dict:
    """Core puro de _scan_cfg_on_demand (puede lanzar)."""
    from fast_mcp.features.code_analysis.application.build_service import BuildGraphUseCase
    from fast_mcp.shared_kernel.paths import resolve_project_root

    project_dir = resolve_project_root(chain_dir)
    if project_dir and os.path.isdir(project_dir):
        # Build a minimal build_service to run scan_cfg
        builder = BuildGraphUseCase(
            project_id=os.path.basename(project_dir),
            data_dir=os.path.join(project_dir, "data"),
            code_root=project_dir,
        )
        return builder.scan_cfg()
    return {}


@safe
def _scan_dfg_on_demand_core(chain_dir: str) -> dict:
    """Core puro de _scan_dfg_on_demand (puede lanzar)."""
    from fast_mcp.features.code_analysis.application.build_service import BuildGraphUseCase
    from fast_mcp.shared_kernel.paths import resolve_project_root

    project_dir = resolve_project_root(chain_dir)
    if project_dir and os.path.isdir(project_dir):
        builder = BuildGraphUseCase(
            project_id=os.path.basename(project_dir),
            data_dir=os.path.join(project_dir, "data"),
            code_root=project_dir,
        )
        return builder.scan_dfg()
    return {}
