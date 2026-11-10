"""SQLite storage and incident evaluation, one transaction per run (SPEC §6.2, severity.md)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable

from .inventory import Host
from .models import OK, Result, utc_iso, worse

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    slot            INTEGER PRIMARY KEY,
    started_at      TEXT NOT NULL,
    committed_at    TEXT NOT NULL,
    elapsed_seconds REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS hosts (
    name    TEXT PRIMARY KEY,
    address TEXT NOT NULL,
    role    TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS incidents (
    id                INTEGER PRIMARY KEY,
    host              TEXT NOT NULL,
    check_name        TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
    opening_severity  TEXT NOT NULL,
    severity          TEXT NOT NULL,
    opened_slot       INTEGER NOT NULL,
    opened_at         TEXT NOT NULL,
    resolved_slot     INTEGER,
    resolved_at       TEXT,
    opening_evidence  TEXT NOT NULL,
    resolution_notes  TEXT NOT NULL DEFAULT '',
    open_result_1     INTEGER NOT NULL,
    open_result_2     INTEGER NOT NULL,
    resolve_result_1  INTEGER,
    resolve_result_2  INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_incident_per_key
    ON incidents (host, check_name) WHERE status = 'open';
CREATE TABLE IF NOT EXISTS check_results (
    id          INTEGER PRIMARY KEY,
    slot        INTEGER NOT NULL REFERENCES runs (slot),
    host        TEXT NOT NULL,
    check_name  TEXT NOT NULL,
    severity    TEXT NOT NULL CHECK (severity IN ('OK', 'Warning', 'Critical', 'Unknown')),
    applicable  INTEGER NOT NULL CHECK (applicable IN (0, 1)),
    value       TEXT NOT NULL,
    evidence    TEXT NOT NULL CHECK (evidence <> ''),
    timestamp   TEXT NOT NULL,
    incident_id INTEGER REFERENCES incidents (id),
    UNIQUE (slot, host, check_name)
);
CREATE TABLE IF NOT EXISTS incident_events (
    id          INTEGER PRIMARY KEY,
    incident_id INTEGER NOT NULL REFERENCES incidents (id),
    slot        INTEGER NOT NULL,
    timestamp   TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('opened', 'severity_changed', 'resolved')),
    severity    TEXT NOT NULL,
    result_id   INTEGER NOT NULL REFERENCES check_results (id)
);
CREATE TABLE IF NOT EXISTS streaks (
    host                 TEXT NOT NULL,
    check_name           TEXT NOT NULL,
    last_slot            INTEGER NOT NULL,
    open_count           INTEGER NOT NULL DEFAULT 0,
    open_first_result    INTEGER,
    recover_count        INTEGER NOT NULL DEFAULT 0,
    recover_first_result INTEGER,
    PRIMARY KEY (host, check_name)
);
"""


RETENTION_DAYS = 30
RETENTION_SLOTS = RETENTION_DAYS * 24 * 12  # 5-minute slots

# Results an incident or a streak still points at are kept past retention, so every
# incident keeps its opening and resolving evidence and streaks stay valid.
PRUNE_SQL = """
DELETE FROM check_results
WHERE slot < ?
  AND id NOT IN (SELECT result_id FROM incident_events)
  AND id NOT IN (SELECT open_result_1 FROM incidents)
  AND id NOT IN (SELECT open_result_2 FROM incidents)
  AND id NOT IN (SELECT resolve_result_1 FROM incidents WHERE resolve_result_1 IS NOT NULL)
  AND id NOT IN (SELECT resolve_result_2 FROM incidents WHERE resolve_result_2 IS NOT NULL)
  AND id NOT IN (SELECT open_first_result FROM streaks WHERE open_first_result IS NOT NULL)
  AND id NOT IN (SELECT recover_first_result FROM streaks WHERE recover_first_result IS NOT NULL)
"""


class RunWriteError(Exception):
    """Writing a run failed; the transaction was rolled back and nothing was kept."""


@dataclass(frozen=True)
class CommitOutcome:
    slot: int
    committed: bool  # False when the slot was already committed (retry): nothing written
    opened: int = 0
    resolved: int = 0
    pruned: int = 0


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def slot_committed(conn: sqlite3.Connection, slot: int) -> bool:
    return conn.execute("SELECT 1 FROM runs WHERE slot = ?", (slot,)).fetchone() is not None


def commit_run(
    conn: sqlite3.Connection,
    slot: int,
    results: Iterable[Result],
    hosts: Iterable[Host],
    started_at: str,
    elapsed_seconds: float,
) -> CommitOutcome:
    """Write a run's results and incident changes atomically. Retrying a slot is a no-op."""
    results = list(results)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if slot_committed(conn, slot):
            conn.execute("ROLLBACK")
            return CommitOutcome(slot, committed=False)
        for h in hosts:
            conn.execute(
                "INSERT INTO hosts (name, address, role) VALUES (?, ?, ?) "
                "ON CONFLICT (name) DO UPDATE SET address = excluded.address, role = excluded.role",
                (h.name, h.address, h.role),
            )
        conn.execute(
            "INSERT INTO runs (slot, started_at, committed_at, elapsed_seconds) VALUES (?, ?, ?, ?)",
            (slot, started_at, utc_iso(), elapsed_seconds),
        )
        opened = resolved = 0
        for r in results:
            cur = conn.execute(
                "INSERT INTO check_results "
                "(slot, host, check_name, severity, applicable, value, evidence, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (slot, r.host, r.check, r.severity, int(r.applicable), r.value, r.evidence, r.timestamp),
            )
            change = _evaluate(conn, slot, cur.lastrowid, r)
            opened += change == "opened"
            resolved += change == "resolved"
        pruned = conn.execute(PRUNE_SQL, (slot - RETENTION_SLOTS,)).rowcount
        conn.execute("COMMIT")
        return CommitOutcome(slot, committed=True, opened=opened, resolved=resolved, pruned=pruned)
    except Exception as exc:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise RunWriteError(f"run for slot {slot} rolled back: {exc}") from exc


def _evaluate(conn: sqlite3.Connection, slot: int, result_id: int, r: Result) -> str | None:
    """Apply severity.md's streak rules for one result. Returns 'opened', 'resolved' or None."""
    key = (r.host, r.check)
    st = conn.execute(
        "SELECT * FROM streaks WHERE host = ? AND check_name = ?", key
    ).fetchone()
    open_count = st["open_count"] if st else 0
    open_first = st["open_first_result"] if st else None
    recover_count = st["recover_count"] if st else 0
    recover_first = st["recover_first_result"] if st else None
    if st is not None and st["last_slot"] != slot - 1:  # missed slot breaks both streaks
        open_count, open_first, recover_count, recover_first = 0, None, 0, None

    inc = conn.execute(
        "SELECT * FROM incidents WHERE host = ? AND check_name = ? AND status = 'open'", key
    ).fetchone()
    change = None

    if not r.applicable:
        open_count, open_first, recover_count, recover_first = 0, None, 0, None
    elif r.severity != OK:
        recover_count, recover_first = 0, None
        if inc is not None:
            _link(conn, result_id, inc["id"])
            if r.severity != inc["severity"]:
                conn.execute("UPDATE incidents SET severity = ? WHERE id = ?", (r.severity, inc["id"]))
                _event(conn, inc["id"], slot, r, "severity_changed", result_id)
        elif open_count == 0:
            open_count, open_first = 1, result_id
        else:
            first = conn.execute("SELECT * FROM check_results WHERE id = ?", (open_first,)).fetchone()
            severity = worse(first["severity"], r.severity)
            evidence = _fmt(first) + "\n\n" + _fmt_result(slot, r)
            cur = conn.execute(
                "INSERT INTO incidents (host, check_name, status, opening_severity, severity, "
                "opened_slot, opened_at, opening_evidence, open_result_1, open_result_2) "
                "VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)",
                (r.host, r.check, severity, severity, slot, r.timestamp, evidence, open_first, result_id),
            )
            _link(conn, open_first, cur.lastrowid)
            _link(conn, result_id, cur.lastrowid)
            _event(conn, cur.lastrowid, slot, r, "opened", result_id, severity)
            open_count, open_first = 0, None
            change = "opened"
    else:  # OK
        open_count, open_first = 0, None
        if inc is not None:
            if recover_count == 0:
                recover_count, recover_first = 1, result_id
            else:
                conn.execute(
                    "UPDATE incidents SET status = 'resolved', resolved_slot = ?, resolved_at = ?, "
                    "resolve_result_1 = ?, resolve_result_2 = ? WHERE id = ?",
                    (slot, r.timestamp, recover_first, result_id, inc["id"]),
                )
                _link(conn, recover_first, inc["id"])
                _link(conn, result_id, inc["id"])
                _event(conn, inc["id"], slot, r, "resolved", result_id)
                recover_count, recover_first = 0, None
                change = "resolved"
        else:
            recover_count, recover_first = 0, None

    conn.execute(
        "INSERT INTO streaks (host, check_name, last_slot, open_count, open_first_result, "
        "recover_count, recover_first_result) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (host, check_name) DO UPDATE SET last_slot = excluded.last_slot, "
        "open_count = excluded.open_count, open_first_result = excluded.open_first_result, "
        "recover_count = excluded.recover_count, recover_first_result = excluded.recover_first_result",
        (r.host, r.check, slot, open_count, open_first, recover_count, recover_first),
    )
    return change


def _link(conn: sqlite3.Connection, result_id: int, incident_id: int) -> None:
    conn.execute("UPDATE check_results SET incident_id = ? WHERE id = ?", (incident_id, result_id))


def _event(conn, incident_id, slot, r: Result, kind: str, result_id: int, severity: str | None = None):
    conn.execute(
        "INSERT INTO incident_events (incident_id, slot, timestamp, kind, severity, result_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (incident_id, slot, r.timestamp, kind, severity or r.severity, result_id),
    )


def _fmt(row: sqlite3.Row) -> str:
    return f"[slot {row['slot']} {row['timestamp']}] {row['severity']} {row['value']}\n{row['evidence']}"


def _fmt_result(slot: int, r: Result) -> str:
    return f"[slot {slot} {r.timestamp}] {r.severity} {r.value}\n{r.evidence}"

