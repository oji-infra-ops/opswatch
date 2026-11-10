"""Command line: `python -m opswatch run [--store]`."""

from __future__ import annotations

import time

PROCESS_START = time.monotonic()  # as early as possible: SPEC §6.1 measures from process start

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402

from .inventory import InventoryError, load_inventory  # noqa: E402
from .models import utc_iso  # noqa: E402
from .runner import Probes, run_checks  # noqa: E402
from .schedule import LockHeld, run_lock, slot_for  # noqa: E402
from .store import RunWriteError, commit_run, connect, slot_committed  # noqa: E402

EXIT_OK = 0
EXIT_RUN_ERROR = 1
EXIT_BAD_INVENTORY = 2


def _default_db() -> str:
    return os.path.expanduser(os.environ.get("OPSWATCH_DB", "~/opswatch.db"))


def _default_inventory() -> str:
    return os.environ.get("OPSWATCH_INVENTORY", "context/inventory.yaml")


def main(argv: list[str] | None = None, probes: Probes | None = None,
         process_start: float | None = None, wall_clock=time.time) -> int:
    process_start = PROCESS_START if process_start is None else process_start
    parser = argparse.ArgumentParser(prog="opswatch")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run all checks once")
    run.add_argument("--inventory", default=_default_inventory())
    run.add_argument("--store", action="store_true", help="commit results and incidents for this slot")
    run.add_argument("--db", default=_default_db())
    args = parser.parse_args(argv)

    started_at = utc_iso()
    slot = slot_for(wall_clock())

    try:
        hosts = load_inventory(args.inventory)
    except InventoryError as exc:
        print(f"opswatch: inventory rejected, nothing run: {exc}", file=sys.stderr)
        return EXIT_BAD_INVENTORY

    if not args.store:
        results = run_checks(hosts, probes)
        print(json.dumps([r.to_dict() for r in results], indent=2))
        return EXIT_OK

    try:
        with run_lock(args.db + ".lock"):
            conn = connect(args.db)
            try:
                if slot_committed(conn, slot):
                    print(f"opswatch: slot {slot} already committed; nothing to do", file=sys.stderr)
                    return EXIT_OK
                results = run_checks(hosts, probes)
                elapsed = time.monotonic() - process_start
                outcome = commit_run(conn, slot, results, hosts, started_at, elapsed)
                total = time.monotonic() - process_start  # process start through commit
            finally:
                conn.close()
    except LockHeld as exc:
        print(f"opswatch: slot {slot} skipped: {exc}", file=sys.stderr)
        return EXIT_OK
    except RunWriteError as exc:
        print(f"opswatch: run-level error: {exc}", file=sys.stderr)
        return EXIT_RUN_ERROR

    summary = {
        "slot": slot,
        "committed": outcome.committed,
        "results": len(results),
        "incidents_opened": outcome.opened,
        "incidents_resolved": outcome.resolved,
        "results_pruned": outcome.pruned,
        "seconds_from_process_start_to_commit": round(total, 3),
        "not_ok": [f"{r.host}/{r.check}: {r.severity} {r.value}" for r in results if r.severity != "OK"],
    }
    print(json.dumps(summary, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
