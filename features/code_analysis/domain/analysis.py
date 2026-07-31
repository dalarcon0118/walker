"""domain/analysis.py — Analysis findings as domain entities."""

from __future__ import annotations
from dataclasses import dataclass


@dataclass
class BugFinding:
    """A bug found during CPG analysis."""

    bug_type: str  # dead_branches, missing_returns, infinite_loops, unused_functions
    function: str
    file: str
    line: int = 0
    detail: str = ""
    is_private: bool = False
    is_method: bool = False
    incoming_calls: int = 0
    incoming_imports: int = 0
    parent_class: str = ""
    parent_type: str = ""

    def to_dict(self) -> dict:
        return {
            "bug": self.bug_type,
            "function": self.function,
            "file": self.file,
            "line": self.line,
            "detail": self.detail,
            "is_private": self.is_private,
            "is_method": self.is_method,
            "incoming_calls": self.incoming_calls,
            "incoming_imports": self.incoming_imports,
            "parent_type": self.parent_type,
            "parent_class": self.parent_class,
        }


@dataclass
class SecurityFinding:
    """A security vulnerability found during CPG analysis."""

    finding_type: str  # taint_flow, hardcoded_secret
    risk: str
    file: str
    line: int = 0
    detail: str = ""
    variable: str = ""
    flow: str = ""

    def to_dict(self) -> dict:
        d = {
            "type": self.finding_type,
            "risk": self.risk,
            "file": self.file,
            "line": self.line,
            "detail": self.detail,
        }
        if self.variable:
            d["variable"] = self.variable
        if self.flow:
            d["flow"] = self.flow
        return d


@dataclass
class DeadCodeCandidate:
    """A function that may be dead code, with context for classification."""

    function: str
    file: str
    line: int
    is_private: bool
    is_method: bool
    incoming_calls: int
    incoming_imports: int
    parent_class: str
    parent_type: str

    def classify(self) -> str:
        """Classify as dead_code, unintegrated, or dynamic_dispatch.

        The agent should override this with rules from task manifest.
        This is a heuristic fallback.
        """
        if self.incoming_calls > 0:
            return "called"
        return "dead_code" if self.is_private else "unintegrated"

    def to_dict(self) -> dict:
        return {
            "bug": "unused_functions",
            "function": self.function,
            "file": self.file,
            "line": self.line,
            "is_private": self.is_private,
            "is_method": self.is_method,
            "incoming_calls": self.incoming_calls,
            "incoming_imports": self.incoming_imports,
            "parent_type": self.parent_type,
            "parent_class": self.parent_class,
        }
