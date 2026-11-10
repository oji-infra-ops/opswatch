"""HTTP and TLS checks against local servers (no lab, no internet)."""

from __future__ import annotations

import shutil
import socket
import ssl
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import make_host

from opswatch import netchecks
from opswatch.models import Observation
from opswatch.netchecks import (
    check_cert,
    check_http,
    classify_cert,
    classify_http,
    probe_cert,
    probe_http,
)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/nowhere")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/slow-body":
            self.send_response(200)
            self.send_header("Content-Length", "5")
            self.end_headers()
            self.wfile.flush()
            time.sleep(3)
            self.wfile.write(b"hello")
        elif self.path == "/missing":
            self.send_error(404)
        else:
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def black_hole():
    """Accepts connections and never answers."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    yield f"http://127.0.0.1:{sock.getsockname()[1]}/"
    sock.close()


# ------------------------------------------------------------ HTTP

def test_redirect_is_not_followed(server):
    obs = probe_http(server + "/redirect")
    assert obs.severity == "OK" and obs.value.startswith("302")


def test_headers_before_slow_body_are_timed_at_headers(server):
    t0 = time.monotonic()
    obs = probe_http(server + "/slow-body")
    assert obs.severity == "OK"
    assert time.monotonic() - t0 < 2.5, "must not wait for the body"


def test_4xx_is_critical(server):
    assert probe_http(server + "/missing").severity == "Critical"


def test_exact_two_seconds_is_ok_and_just_over_is_warning(server):
    for elapsed, expected in ((2.0, "OK"), (2.001, "Warning")):
        ticks = iter([0.0, 100.0, 100.0 + elapsed, 200.0, 200.0])
        obs = probe_http(server + "/", clock=lambda t=ticks: next(t))
        assert obs.severity == expected, elapsed
    assert classify_http(200, 2.0).severity == "OK"
    assert classify_http(399, 2.0000001).severity == "Warning"


def test_deadline_expiry_is_critical(black_hole):
    t0 = time.monotonic()
    obs = probe_http(black_hole, deadline=0.5)
    assert obs.severity == "Critical" and obs.value == "deadline expired"
    assert time.monotonic() - t0 < 1.5


def test_dns_failure_is_unknown(monkeypatch):
    def fail(*a, **k):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    obs = probe_http("http://no-such-host.invalid/")
    assert (obs.severity, obs.value) == ("Unknown", "DNS failure")


def test_connection_refused_is_critical():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert probe_http(f"http://127.0.0.1:{port}/").severity == "Critical"


def test_malformed_url_is_unknown_and_others_still_run(server):
    host = make_host(urls=["ftp://x/", server + "/"])
    obs = check_http(host)
    assert obs.severity == "Unknown"
    assert "ftp://x/" in obs.evidence and server in obs.evidence


def test_no_urls_is_not_applicable():
    obs = check_http(make_host(urls=[]))
    assert (obs.applicable, obs.severity, obs.value) == (False, "Unknown", "Not applicable")


def test_url_rollup_critical_beats_unknown_and_ties_use_inventory_order():
    verdicts = {
        "https://a/": Observation("Unknown", "dns", "e"),
        "https://b/": Observation("Critical", "HTTP 500", "e"),
        "https://c/": Observation("Critical", "HTTP 503", "e"),
    }
    host = make_host(urls=list(verdicts))
    obs = check_http(host, probe=lambda u, ca: verdicts[u])
    assert obs.severity == "Critical" and obs.value.startswith("https://b/")
    for u in verdicts:
        assert u in obs.evidence


def test_stalled_url_does_not_block_other_urls():
    def probe(u, ca):
        if "stall" in u:
            time.sleep(1.0)
            return Observation("Critical", "deadline expired", "e")
        return Observation("OK", "200", "e")

    urls = ["http://stall/", "http://a/", "http://b/", "http://c/", "http://d/"]
    t0 = time.monotonic()
    check_http(make_host(urls=urls), probe=probe)
    assert time.monotonic() - t0 < 1.5  # bounded concurrency, not serial


# ------------------------------------------------------------ certificate thresholds

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "remaining, severity, value",
    [
        (timedelta(days=2, hours=23), "Critical", "2 days"),
        (timedelta(days=3), "Warning", "3 days"),
        (timedelta(days=13, hours=23, minutes=59), "Warning", "13 days"),
        (timedelta(days=14), "OK", "14 days"),
        (timedelta(0), "Critical", "expired"),
        (timedelta(seconds=-1), "Critical", "expired"),
    ],
)
def test_cert_thresholds(remaining, severity, value):
    obs = classify_cert(NOW + remaining, NOW)
    assert (obs.severity, obs.value) == (severity, value)
    assert "notAfter=" in obs.evidence


def _verify_error(code: int, message: str) -> ssl.SSLCertVerificationError:
    err = ssl.SSLCertVerificationError(1, message)
    err.verify_code = code
    err.verify_message = message
    return err


def test_expired_cert_with_another_validation_error_is_unknown():
    def handshake(host, port, ca, ignore_time, timeout):
        if not ignore_time:
            raise _verify_error(10, "certificate has expired")
        raise _verify_error(62, "hostname mismatch")

    obs = probe_cert("https://web01/", handshake=handshake)
    assert obs.severity == "Unknown"
    assert "hostname mismatch" in obs.evidence


def test_expired_cert_as_sole_failure_is_critical():
    def handshake(host, port, ca, ignore_time, timeout):
        if not ignore_time:
            raise _verify_error(10, "certificate has expired")
        return {"notAfter": "Nov  1 00:00:00 2026 GMT"}

    obs = probe_cert("https://web01/", handshake=handshake)
    assert (obs.severity, obs.value) == ("Critical", "expired")
    assert "notAfter=2026-11-01" in obs.evidence


def test_cert_only_http_urls_is_not_applicable():
    obs = check_cert(make_host(urls=["http://web01/"]))
    assert obs.applicable is False and obs.value == "Not applicable"


# ------------------------------------------------------------ real TLS with a generated cert

@pytest.fixture(scope="module")
def tls_server(tmp_path_factory):
    if not shutil.which("openssl"):
        pytest.skip("openssl binary not available")
    d = tmp_path_factory.mktemp("tls")
    key, crt = d / "web01.key", d / "web01.crt"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "10",
         "-subj", "/CN=web01", "-addext", "subjectAltName=DNS:web01,DNS:localhost",
         "-addext", "basicConstraints=critical,CA:TRUE",
         "-addext", "keyUsage=critical,digitalSignature,keyCertSign",
         "-keyout", str(key), "-out", str(crt)],
        check=True, capture_output=True,
    )
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(crt, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], str(crt)
    srv.shutdown()


def test_tls_trusted_by_host_ca_cert(tls_server):
    port, crt = tls_server
    obs = probe_http(f"https://localhost:{port}/", ca_cert=crt)
    assert obs.severity == "OK"
    cert = probe_cert(f"https://localhost:{port}/", ca_cert=crt)
    assert (cert.severity, cert.value) == ("Warning", "9 days")


def test_tls_hostname_mismatch(tls_server):
    port, crt = tls_server
    # 127.0.0.1 isn't in the certificate's subjectAltName.
    assert probe_http(f"https://127.0.0.1:{port}/", ca_cert=crt).severity == "Critical"
    assert probe_cert(f"https://127.0.0.1:{port}/", ca_cert=crt).severity == "Unknown"


def test_tls_untrusted_without_ca_cert(tls_server):
    port, _ = tls_server
    assert probe_http(f"https://localhost:{port}/").severity == "Critical"


def test_module_constants():
    assert netchecks.URL_DEADLINE_SECONDS == 5.0
