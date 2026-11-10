from __future__ import annotations

import pytest

from opswatch.inventory import Host
from opswatch.models import Result
from opswatch.store import connect


def make_host(name="web01", services=(), urls=(), ca_cert=None, address="192.0.2.10") -> Host:
    return Host(
        name=name,
        address=address,
        role="test",
        ssh_user="opswatch",
        ssh_key="~/.ssh/opswatch",
        services=tuple(services),
        urls=tuple(urls),
        ca_cert=ca_cert,
    )


LAB = [
    make_host("web01", services=["nginx"], urls=["https://web01/"]),
    make_host("app01", services=["opswatch-demo"]),
    make_host("stor01"),
]


def result(severity="OK", host="stor01", check="disk", value="v", applicable=True,
           ts="2026-11-09T00:00:00Z") -> Result:
    return Result(host, check, severity, applicable, value, f"evidence for {severity}", ts)


@pytest.fixture
def db(tmp_path):
    conn = connect(str(tmp_path / "opswatch.db"))
    yield conn
    conn.close()
