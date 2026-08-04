"""application/bug_service.py — ScanBugsUseCase + ScanSecurityUseCase.

Bug detection and security scanning using CPG + CFG/DFG analysis.
"""

from __future__ import annotations
import os

from features.code_analysis.domain.graph import Graph, Node
from features.code_analysis.domain.query import Query, Filter
from features.code_analysis.infrastructure.repository import CpgFileRepository
from features.code_analysis.infrastructure.filters import match_file_filter


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

    def __init__(self, chain_dir: str, repo: CpgFileRepository, graph: Graph):
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
            data = self._repo.load()
            cfg_data = data.get("analysis_results", {})
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
            data = self._repo.load()
            dfg_data = data.get("dfg", {})
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
        from features.code_analysis.domain.analysis import (
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
        """Trigger CFG scan if not already present."""
        try:
            from features.code_analysis.application.build_service import BuildGraphUseCase
            from libs.path_resolver import resolve_project_root

            project_dir = resolve_project_root(self.chain_dir)
            if project_dir and os.path.isdir(project_dir):
                # Build a minimal build_service to run scan_cfg
                builder = BuildGraphUseCase(
                    project_id=os.path.basename(project_dir),
                    data_dir=os.path.join(project_dir, "data"),
                    code_root=project_dir,
                )
                return builder.scan_cfg()
        except Exception:
            pass
        return {}

    # ── Security ─────────────────────────────────────────────────────

    def handle_security(self, query: Query) -> dict | None:
        target = query.get("security")
        if not target:
            return None

        attr_filters = query.exclude_keys({"security"})

        data = self._repo.load()
        dfg_data = data.get("dfg", {})
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
