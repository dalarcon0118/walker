"""domain/metrics.py — Code quality metrics and quality gates.

Value objects that represent measurements and quality thresholds.
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class FileMetrics:
    """Metrics for a single file."""

    file: str
    loc: int = 0
    functions: int = 0
    classes: int = 0
    imports: int = 0
    cyclo_avg: float = 0.0
    cyclo_max: int = 0
    coupling: int = 0
    complexity_rating: str = "A"

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "loc": self.loc,
            "functions": self.functions,
            "classes": self.classes,
            "imports": self.imports,
            "cyclo_avg": round(self.cyclo_avg, 2),
            "cyclo_max": self.cyclo_max,
            "coupling": self.coupling,
            "rating": self.complexity_rating,
        }


@dataclass
class ProjectMetrics:
    """Aggregated metrics for the whole project."""

    total_files: int = 0
    total_loc: int = 0
    total_functions: int = 0
    total_classes: int = 0
    total_imports: int = 0
    cyclo_avg: float = 0.0
    cyclo_max: int = 0
    dead_code_count: int = 0
    security_findings: int = 0
    files: list[FileMetrics] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "files": self.total_files,
            "loc": self.total_loc,
            "functions": self.total_functions,
            "classes": self.total_classes,
            "imports": self.total_imports,
            "cyclo_avg": round(self.cyclo_avg, 2),
            "cyclo_max": self.cyclo_max,
            "dead_code": self.dead_code_count,
            "security": self.security_findings,
            "per_file": [f.to_dict() for f in self.files[:50]],
        }


@dataclass
class QualityGate:
    """A single quality gate with threshold."""

    name: str
    actual: float | int
    threshold: float | int
    passed: bool

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "actual": self.actual,
            "threshold": self.threshold,
            "passed": self.passed,
        }


@dataclass
class ReviewResult:
    """Result of a code review execution."""

    passed: bool
    metrics: ProjectMetrics
    gates: list[QualityGate]
    findings: dict[str, list[dict]] = field(default_factory=dict)
    recommendations: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    files_scanned: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "metrics": self.metrics.to_dict(),
            "gates": [g.to_dict() for g in self.gates],
            "findings": self.findings,
            "recommendations": self.recommendations,
            "issues": self.issues[:50],
            "files_scanned": self.files_scanned,
            "summary": self.summary,
        }
