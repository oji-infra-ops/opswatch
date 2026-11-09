"""Disk, services and uptime: run over SSH, parsed by pure functions (SPEC §4.1-4.3)."""

from __future__ import annotations

import math
import re
import shlex

from .inventory import Host
from .models import CRITICAL, OK, UNKNOWN, WARNING, Observation, worst_index
from .sshrun import SSH_DEADLINE_SECONDS, CmdResult, Runner, run_with_deadline, ssh_argv

# ---------------------------------------------------------------- disk

DF_COMMAND = "LC_ALL=C df -P"
DF_HEADER = ["Filesystem", "1024-blocks", "Used", "Available", "Capacity", "Mounted", "on"]
DF_EXCLUDED_SOURCES = {"tmpfs", "devtmpfs"}  # matched against the first column exactly
_PCT = re.compile(r"\d+%")


def parse_disk(stdout: str) -> Observation:
    lines = stdout.splitlines()
    raw = f"df -P output:\n{stdout}" if stdout.strip() else "df -P printed nothing"
    if not lines or lines[0].split() != DF_HEADER:
        return Observation(UNKNOWN, "unexpected df header", raw)

    mounts: list[tuple[str, int]] = []
    malformed: list[str] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split(None, 5)
        if fields and fields[0] in DF_EXCLUDED_SOURCES:
            continue
        if len(fields) != 6 or not _PCT.fullmatch(fields[4]):
            malformed.append(line)
            continue
        mounts.append((fields[5], int(fields[4][:-1])))

    if malformed:
        return Observation(
            UNKNOWN,
            f"{len(malformed)} malformed df row(s)",
            "malformed rows:\n" + "\n".join(malformed) + "\n\n" + raw,
        )
    if not mounts:
        return Observation(UNKNOWN, "no mounts after excluding tmpfs/devtmpfs", raw)

    best_mount, best_pct = mounts[0]
    for mount, pct in mounts[1:]:
        if pct > best_pct:  # strict: ties keep the first row in output order
            best_mount, best_pct = mount, pct
    if best_pct >= 90:
        severity = CRITICAL
    elif best_pct >= 80:
        severity = WARNING
    else:
        severity = OK
    return Observation(severity, f"{best_mount} {best_pct}%", raw)


def check_disk(host: Host, run: Runner = run_with_deadline) -> Observation:
    res = run(ssh_argv(host, DF_COMMAND), SSH_DEADLINE_SECONDS)
    if res.transport_failed:
        return Observation(UNKNOWN, "ssh failed", res.failure_text())
    if res.returncode != 0:
        return Observation(
            UNKNOWN,
            f"df exited {res.returncode}",
            f"stderr: {res.stderr.strip() or '(empty)'}\nstdout:\n{res.stdout}",
        )
    return parse_disk(res.stdout)


# ---------------------------------------------------------------- services

def services_script(units: tuple[str, ...]) -> str:
    """One shell script, one SSH session, both parts (SPEC §4.2)."""
    parts = [
        "export LC_ALL=C",
        "echo @@FAILED",
        "systemctl --failed --no-legend --plain",
        "echo @@RC=$?",
    ]
    for i, unit in enumerate(units):
        parts += [f"echo @@UNIT={i}", f"systemctl is-active -- {shlex.quote(unit)}", "echo @@RC=$?"]
    parts.append("echo @@END")
    return "; ".join(parts)


class _MarkerError(ValueError):
    pass


def _sections(stdout: str) -> list[tuple[str, list[str], int]]:
    """Split marked output into (marker, body_lines, exit_code) tuples."""
    sections: list[tuple[str, list[str], int]] = []
    current: str | None = None
    body: list[str] = []
    ended = False
    for line in stdout.splitlines():
        if line in ("@@FAILED",) or line.startswith("@@UNIT="):
            if current is not None:
                raise _MarkerError(f"section {current} has no exit code")
            current, body = line, []
        elif line.startswith("@@RC="):
            if current is None:
                raise _MarkerError("exit code outside a section")
            try:
                rc = int(line[5:])
            except ValueError as exc:
                raise _MarkerError(f"bad exit code line {line!r}") from exc
            sections.append((current, body, rc))
            current = None
        elif line == "@@END":
            ended = True
        elif current is not None:
            body.append(line)
    if current is not None or not ended:
        raise _MarkerError("output ended before all sections completed")
    return sections


def parse_services(stdout: str, stderr: str, units: tuple[str, ...]) -> Observation:
    raw = f"stdout:\n{stdout}\nstderr:\n{stderr.strip() or '(empty)'}"
    try:
        sections = _sections(stdout)
    except _MarkerError as exc:
        return Observation(UNKNOWN, "incomplete systemctl output", f"{exc}\n{raw}")
    expected = ["@@FAILED"] + [f"@@UNIT={i}" for i in range(len(units))]
    if [s[0] for s in sections] != expected:
        return Observation(UNKNOWN, "incomplete systemctl output", f"sections out of order\n{raw}")

    observations: list[Observation] = []
    _, failed_body, failed_rc = sections[0]
    if failed_rc != 0:
        observations.append(Observation(UNKNOWN, f"systemctl --failed exited {failed_rc}", raw))
    else:
        failed = [ln.split()[0] for ln in failed_body if ln.strip()]
        if failed:
            observations.append(Observation(CRITICAL, "failed: " + ", ".join(failed), raw))
        else:
            observations.append(Observation(OK, "no failed units", raw))

    for unit, (_, body, rc) in zip(units, sections[1:], strict=True):
        state = "\n".join(body).strip()
        if not state:
            observations.append(Observation(UNKNOWN, f"{unit} no is-active output (exit {rc})", raw))
        elif state == "active":
            observations.append(Observation(OK, f"{unit} active", raw))
        else:
            observations.append(Observation(CRITICAL, f"{unit} {state}", raw))

    chosen = observations[worst_index(o.severity for o in observations)]
    summary = "\n".join(f"{o.severity}: {o.value}" for o in observations)
    return Observation(chosen.severity, chosen.value, f"{summary}\n\n{raw}")


def check_services(host: Host, run: Runner = run_with_deadline) -> Observation:
    res: CmdResult = run(ssh_argv(host, services_script(host.services)), SSH_DEADLINE_SECONDS)
    if res.transport_failed:
        return Observation(UNKNOWN, "ssh failed", res.failure_text())
    return parse_services(res.stdout, res.stderr, host.services)


# ---------------------------------------------------------------- uptime

UPTIME_COMMAND = "cat /proc/uptime"


def parse_uptime(stdout: str) -> Observation:
    raw = f"/proc/uptime: {stdout.strip() or '(empty)'}"
    fields = stdout.split()
    try:
        seconds = float(fields[0])
    except (IndexError, ValueError):
        return Observation(UNKNOWN, "unparseable uptime", raw)
    if not math.isfinite(seconds) or seconds < 0:
        return Observation(UNKNOWN, "invalid uptime value", raw)
    hours = seconds / 3600
    value = f"up {hours:.1f} h"
    return Observation(WARNING if seconds < 3600 else OK, value, raw)


def check_uptime(host: Host, run: Runner = run_with_deadline) -> Observation:
    res = run(ssh_argv(host, UPTIME_COMMAND), SSH_DEADLINE_SECONDS)
    if res.transport_failed:
        return Observation(UNKNOWN, "ssh failed", res.failure_text())
    if res.returncode != 0:
        return Observation(UNKNOWN, f"cat exited {res.returncode}", res.stderr.strip() or "(no stderr)")
    return parse_uptime(res.stdout)
