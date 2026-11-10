"""Result types, severities and the rollup order (SPEC §3, severity.md)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable

OK = "OK"
WARNING = "Warning"
UNKNOWN = "Unknown"
CRITICAL = "Critical"
SEVERITIES = (OK, WARNING, UNKNOWN, CRITICAL)

# Rollup and comparison order: Critical > Unknown > Warning > OK.
RANK = {OK: 0, WARNING: 1, UNKNOWN: 2, CRITICAL: 3}

CHECKS = ("disk", "services", "uptime", "http", "cert")

NOT_APPLICABLE = "Not applicable"


@dataclass(frozen=True)
class Observation:
    """One judged thing: a mount, a unit, a URL, or a whole check."""

    severity: str
    value: str
    evidence: str
    applicable: bool = True

    def __post_init__(self) -> None:
        if self.severity not in RANK:
            raise ValueError(f"bad severity {self.severity!r}")
        if not self.evidence:
            raise ValueError("evidence must not be empty")


def inapplicable(reason: str) -> Observation:
    return Observation(UNKNOWN, NOT_APPLICABLE, reason, applicable=False)


@dataclass(frozen=True)
class Result:
    host: str
    check: str
    severity: str
    applicable: bool
    value: str
    evidence: str
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)


def worse(a: str, b: str) -> str:
    """The higher of two severities under the rollup order (ties keep `a`)."""
    return b if RANK[b] > RANK[a] else a


def worst_index(severities: Iterable[str]) -> int:
    """Index of the worst severity; ties go to the earliest item."""
    best_i, best_rank = -1, -1
    for i, sev in enumerate(severities):
        if RANK[sev] > best_rank:
            best_i, best_rank = i, RANK[sev]
    if best_i < 0:
        raise ValueError("no severities to roll up")
    return best_i


def utc_iso(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
