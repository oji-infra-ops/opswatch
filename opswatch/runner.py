"""Run every check for every host concurrently; one result per host per check (SPEC §3, §6.1)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

from . import netchecks, sshchecks
from .inventory import Host
from .models import CHECKS, UNKNOWN, Observation, Result, utc_iso

CheckFn = Callable[[Host], Observation]


@dataclass
class Probes:
    """The five check functions. Tests swap these for stubs."""

    disk: CheckFn = sshchecks.check_disk
    services: CheckFn = sshchecks.check_services
    uptime: CheckFn = sshchecks.check_uptime
    http: CheckFn = netchecks.check_http
    cert: CheckFn = netchecks.check_cert

    def get(self, check: str) -> CheckFn:
        return getattr(self, check)


def _run_one(host: Host, check: str, fn: CheckFn) -> Result:
    try:
        obs = fn(host)
    except Exception as exc:  # noqa: BLE001 - a broken check must not break the run
        obs = Observation(UNKNOWN, "check raised an exception", f"{type(exc).__name__}: {exc}")
    return Result(
        host=host.name,
        check=check,
        severity=obs.severity,
        applicable=obs.applicable,
        value=obs.value,
        evidence=obs.evidence,
        timestamp=utc_iso(),  # completion time of this check
    )


def run_checks(hosts: list[Host], probes: Probes | None = None) -> list[Result]:
    """Results in inventory order, then check order; all checks run in parallel."""
    probes = probes or Probes()
    jobs = [(h, c) for h in hosts for c in CHECKS]
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [pool.submit(_run_one, h, c, probes.get(c)) for h, c in jobs]
        return [f.result() for f in futures]
