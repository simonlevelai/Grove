"""Grove health check module.

Public API:

- ``HealthReporter`` -- orchestrates all checks, aggregates results.
- ``HealthReport`` / ``CheckResult`` -- Pydantic result models.
- Individual checkers for targeted use.
"""

from grove.health.contradictions import ContradictionDetector
from grove.health.gaps import GapDetector
from grove.health.models import CheckResult, HealthReport
from grove.health.orphans import OrphanDetector
from grove.health.provenance import ProvenanceChecker
from grove.health.reporter import HealthReporter
from grove.health.staleness import StalenessChecker

__all__ = [
    "CheckResult",
    "ContradictionDetector",
    "GapDetector",
    "HealthReport",
    "HealthReporter",
    "OrphanDetector",
    "ProvenanceChecker",
    "StalenessChecker",
]
