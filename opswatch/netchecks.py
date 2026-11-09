"""HTTP availability and certificate expiry, probed from ops-ctl (SPEC §4.4-4.5)."""

from __future__ import annotations

import errno
import http.client
import math
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlsplit

from .inventory import Host
from .models import CRITICAL, OK, UNKNOWN, WARNING, Observation, inapplicable, worst_index

URL_DEADLINE_SECONDS = 5.0
SLOW_RESPONSE_SECONDS = 2.0
URL_CONCURRENCY = 4
X509_V_ERR_CERT_HAS_EXPIRED = 10
X509_V_FLAG_NO_CHECK_TIME = 0x200000  # OpenSSL flag; not exposed by name in Python's ssl
_NO_ROUTE = {errno.EHOSTUNREACH, errno.ENETUNREACH}


class DeadlineExpired(Exception):
    pass


class MalformedURL(ValueError):
    pass


def call_with_deadline(fn: Callable[[], object], deadline: float):
    """Run fn in a daemon thread; raise DeadlineExpired if it isn't done in time.

    Socket-level timeouts can't bound DNS lookups, so the deadline is enforced from
    outside. A thread that overruns is abandoned; its socket timeouts end it soon after.
    """
    box: dict = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            box["error"] = exc

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(deadline)
    if t.is_alive():
        raise DeadlineExpired(f"no answer within {deadline:g}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def url_parts(url: str) -> tuple[str, str, int, str]:
    """(scheme, host, port, path) or MalformedURL."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise MalformedURL(str(exc)) from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise MalformedURL("needs an http:// or https:// scheme and a host")
    port = port or (443 if parts.scheme == "https" else 80)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return parts.scheme, parts.hostname, port, path


def url_kind(url: str) -> str:
    try:
        return url_parts(url)[0]
    except MalformedURL:
        return "malformed"


def tls_context(ca_cert: str | None, ignore_time: bool = False) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=ca_cert) if ca_cert else ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    if ignore_time:
        ctx.verify_flags = ctx.verify_flags | X509_V_FLAG_NO_CHECK_TIME
    return ctx


def _roll_up(observations: list[tuple[str, Observation]]) -> Observation:
    """Worst URL wins; ties go to inventory order. Evidence keeps every URL."""
    i = worst_index(o.severity for _, o in observations)
    url, chosen = observations[i]
    evidence = "\n".join(f"{u}: {o.severity} {o.value} | {o.evidence}" for u, o in observations)
    return Observation(chosen.severity, f"{url} {chosen.value}", evidence)


def _probe_all(urls: list[str], probe: Callable[[str], Observation]) -> list[tuple[str, Observation]]:
    with ThreadPoolExecutor(max_workers=min(URL_CONCURRENCY, len(urls))) as pool:
        return list(zip(urls, pool.map(probe, urls), strict=True))


# ---------------------------------------------------------------- HTTP

def classify_http(status: int, elapsed: float) -> Observation:
    evidence = f"status {status}, {elapsed:.3f}s to headers"
    if 200 <= status < 400:
        if elapsed <= SLOW_RESPONSE_SECONDS:
            return Observation(OK, f"{status} in {elapsed:.2f}s", evidence)
        return Observation(WARNING, f"{status} slow ({elapsed:.2f}s)", evidence)
    if 400 <= status < 600:
        return Observation(CRITICAL, f"HTTP {status}", evidence)
    return Observation(UNKNOWN, f"unexpected status {status}", evidence)


def probe_http(
    url: str,
    ca_cert: str | None = None,
    deadline: float = URL_DEADLINE_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> Observation:
    try:
        scheme, host, port, path = url_parts(url)
    except MalformedURL as exc:
        return Observation(UNKNOWN, "malformed URL", f"{url!r}: {exc}")

    started = clock()

    def get() -> tuple[int, float]:
        if scheme == "https":
            conn = http.client.HTTPSConnection(host, port, timeout=deadline, context=tls_context(ca_cert))
        else:
            conn = http.client.HTTPConnection(host, port, timeout=deadline)
        try:
            t0 = clock()  # immediately before the GET starts
            conn.request("GET", path, headers={"User-Agent": "opswatch/0.1", "Connection": "close"})
            resp = conn.getresponse()  # returns once headers have arrived
            elapsed = clock() - t0
            status = resp.status
            resp.close()  # don't wait for the body
            return status, elapsed
        finally:
            conn.close()

    try:
        status, elapsed = call_with_deadline(get, deadline)
    except DeadlineExpired as exc:
        return Observation(CRITICAL, "deadline expired", f"{exc}; elapsed {clock() - started:.3f}s")
    except ssl.SSLCertVerificationError as exc:
        return Observation(CRITICAL, "TLS validation failed", _err(exc, clock() - started))
    except ConnectionRefusedError as exc:
        return Observation(CRITICAL, "connection refused", _err(exc, clock() - started))
    except TimeoutError as exc:
        return Observation(CRITICAL, "deadline expired", _err(exc, clock() - started))
    except socket.gaierror as exc:
        return Observation(UNKNOWN, "DNS failure", _err(exc, clock() - started))
    except OSError as exc:
        if exc.errno in _NO_ROUTE:
            return Observation(UNKNOWN, "no route to host", _err(exc, clock() - started))
        return Observation(UNKNOWN, "unclassified error", _err(exc, clock() - started))
    except Exception as exc:  # noqa: BLE001 - anything else can't be classified
        return Observation(UNKNOWN, "unclassified error", _err(exc, clock() - started))
    return classify_http(status, elapsed)


def _err(exc: BaseException, elapsed: float) -> str:
    return f"{type(exc).__name__}: {exc}; elapsed {elapsed:.3f}s"


def check_http(host: Host, probe: Callable[..., Observation] = probe_http) -> Observation:
    if not host.urls:
        return inapplicable("no urls configured for host")
    return _roll_up(_probe_all(list(host.urls), lambda u: probe(u, host.ca_cert)))


# ---------------------------------------------------------------- certificate

def _days_text(days: int) -> str:
    return "1 day" if days == 1 else f"{days} days"


def classify_cert(not_after: datetime, now: datetime) -> Observation:
    remaining = not_after - now
    evidence = f"notAfter={not_after.strftime('%Y-%m-%dT%H:%M:%SZ')}, remaining {remaining}"
    if remaining <= timedelta(0):
        return Observation(CRITICAL, "expired", evidence)
    days = math.floor(remaining.total_seconds() / 86400)  # display only
    if remaining < timedelta(days=3):
        return Observation(CRITICAL, _days_text(days), evidence)
    if remaining < timedelta(days=14):
        return Observation(WARNING, _days_text(days), evidence)
    return Observation(OK, _days_text(days), evidence)


def _not_after(cert: dict) -> datetime:
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), timezone.utc)


def _handshake(host: str, port: int, ca_cert: str | None, ignore_time: bool, timeout: float) -> dict:
    ctx = tls_context(ca_cert, ignore_time=ignore_time)
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            cert = tls.getpeercert()
    if not cert or "notAfter" not in cert:
        raise ValueError("validated handshake returned no certificate details")
    return cert


Handshake = Callable[[str, int, "str | None", bool, float], dict]


def probe_cert(
    url: str,
    ca_cert: str | None = None,
    deadline: float = URL_DEADLINE_SECONDS,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    handshake: Handshake = _handshake,
) -> Observation:
    try:
        _, host, port, _ = url_parts(url)
    except MalformedURL as exc:
        return Observation(UNKNOWN, "malformed URL", f"{url!r}: {exc}")

    started = time.monotonic()

    def remaining() -> float:
        return max(0.0, deadline - (time.monotonic() - started))

    try:
        cert = call_with_deadline(lambda: handshake(host, port, ca_cert, False, deadline), deadline)
    except ssl.SSLCertVerificationError as exc:
        if exc.verify_code != X509_V_ERR_CERT_HAS_EXPIRED:
            return Observation(UNKNOWN, "TLS validation failed", f"{exc.verify_message} ({exc})")
        # The library stops at the first error. Re-validate with only the time check off
        # to learn whether expiry is the *sole* failure.
        try:
            left = remaining()
            cert = call_with_deadline(lambda: handshake(host, port, ca_cert, True, left), left)
        except ssl.SSLCertVerificationError as exc2:
            return Observation(
                UNKNOWN,
                "expired and another validation failure",
                f"first: {exc.verify_message}; with time check off: {exc2.verify_message}",
            )
        except Exception as exc2:  # noqa: BLE001
            return Observation(UNKNOWN, "expired; could not confirm other checks", _err(exc2, 0))
        na = _not_after(cert)
        return Observation(
            CRITICAL, "expired", f"notAfter={na.strftime('%Y-%m-%dT%H:%M:%SZ')}; {exc.verify_message}"
        )
    except DeadlineExpired as exc:
        return Observation(UNKNOWN, "handshake deadline expired", str(exc))
    except Exception as exc:  # noqa: BLE001 - can't establish why validation failed
        return Observation(UNKNOWN, "handshake failed", _err(exc, time.monotonic() - started))
    return classify_cert(_not_after(cert), now())


def check_cert(host: Host, probe: Callable[..., Observation] = probe_cert) -> Observation:
    eligible = [u for u in host.urls if url_kind(u) in ("https", "malformed")]
    if not eligible:
        return inapplicable("no https:// urls configured for host")
    return _roll_up(_probe_all(eligible, lambda u: probe(u, host.ca_cert)))
