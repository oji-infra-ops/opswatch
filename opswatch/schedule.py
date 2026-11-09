"""Fixed five-minute slots and the one-run-at-a-time lock (SPEC §6.3)."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from typing import Iterator

SLOT_SECONDS = 300


def slot_for(unix_time: float) -> int:
    return int(unix_time // SLOT_SECONDS)


class LockHeld(Exception):
    """Another OpsWatch run is still active; this slot is skipped."""


@contextmanager
def run_lock(path: str) -> Iterator[None]:
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockHeld(f"lock {path} is held by another run") from exc
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
