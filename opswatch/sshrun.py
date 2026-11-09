"""Run commands with a hard deadline, and build SSH command lines (SPEC §2)."""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from typing import Callable

from .inventory import Host

SSH_DEADLINE_SECONDS = 10.0
SSH_TRANSPORT_FAILURE = 255  # ssh's own exit code for connection/auth errors


@dataclass(frozen=True)
class CmdResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    error: str | None = None

    @property
    def transport_failed(self) -> bool:
        return self.timed_out or self.error is not None or self.returncode == SSH_TRANSPORT_FAILURE

    def failure_text(self) -> str:
        if self.timed_out:
            return f"deadline expired; process killed. stderr: {self.stderr.strip() or '(empty)'}"
        if self.error:
            return f"could not start command: {self.error}"
        return f"ssh exited {self.returncode}: {self.stderr.strip() or '(no stderr)'}"


Runner = Callable[[list[str], float], CmdResult]


def run_with_deadline(argv: list[str], timeout: float) -> CmdResult:
    """Run argv; after `timeout` seconds kill its whole process group."""
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        return CmdResult(None, "", "", error=str(exc))
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = proc.communicate()
        return CmdResult(None, out or "", err or "", timed_out=True)
    return CmdResult(proc.returncode, out, err)


def ssh_argv(host: Host, remote_command: str) -> list[str]:
    return [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "IdentitiesOnly=yes",  # only the inventory key: never wait on an ssh-agent (SPEC §2)
        "-i", os.path.expanduser(host.ssh_key),
        "-l", host.ssh_user,
        host.address,
        remote_command,
    ]
