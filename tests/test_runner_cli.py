import json
import sqlite3
import time

import pytest
from conftest import LAB

from opswatch.__main__ import main
from opswatch.inventory import InventoryError, parse_inventory
from opswatch.models import Observation
from opswatch.netchecks import check_cert, check_http
from opswatch.runner import Probes, run_checks
from opswatch.schedule import run_lock

INVENTORY = """
defaults: {ssh_user: opswatch, ssh_key: ~/.ssh/opswatch}
hosts:
  web01: {address: 192.168.56.11, services: [nginx], urls: ["https://web01/"]}
  app01: {address: 192.168.56.12, services: [opswatch-demo]}
  stor01: {address: 192.168.56.13}
"""


def ok(host):
    return Observation("OK", "fine", "stub")


def stub_probes(**overrides):
    probes = Probes(disk=ok, services=ok, uptime=ok, http=ok, cert=ok)
    for k, v in overrides.items():
        setattr(probes, k, v)
    return probes


# ------------------------------------------------------------ results

def test_lab_run_produces_fifteen_rows_with_real_applicability():
    probes = stub_probes(
        http=lambda h: check_http(h, probe=lambda u, ca: Observation("OK", "200", "e")),
        cert=lambda h: check_cert(h, probe=lambda u, ca: Observation("OK", "40 days", "e")),
    )
    results = run_checks(LAB, probes)
    assert len(results) == 15
    na = {(r.host, r.check) for r in results if not r.applicable}
    assert na == {("app01", "http"), ("app01", "cert"), ("stor01", "http"), ("stor01", "cert")}
    assert all(r.value == "Not applicable" and r.severity == "Unknown" for r in results if not r.applicable)
    assert all(r.timestamp.endswith("Z") for r in results)


def test_parser_exception_becomes_unknown_for_that_check_only():
    def boom(host):
        raise ValueError("bad output")

    results = run_checks(LAB, stub_probes(disk=boom))
    disk = [r for r in results if r.check == "disk"]
    assert all(r.severity == "Unknown" and "ValueError: bad output" in r.evidence for r in disk)
    assert all(r.severity == "OK" for r in results if r.check != "disk")


def test_powered_off_host_does_not_delay_healthy_hosts():
    done = {}

    def ssh_probe(host):
        if host.name == "stor01":
            time.sleep(1.0)  # stands in for the 10 s SSH deadline
            obs = Observation("Unknown", "ssh failed", "deadline expired")
        else:
            obs = Observation("OK", "fine", "stub")
        done[host.name] = time.monotonic()
        return obs

    t0 = time.monotonic()
    results = run_checks(LAB, stub_probes(disk=ssh_probe, services=ssh_probe, uptime=ssh_probe))
    total = time.monotonic() - t0
    assert total < 1.5, "hosts and checks must run in parallel"
    assert done["web01"] - t0 < 0.3 and done["app01"] - t0 < 0.3
    ssh_checks = ("disk", "services", "uptime")
    assert {r.severity for r in results if r.host == "stor01" and r.check in ssh_checks} == {"Unknown"}


# ------------------------------------------------------------ inventory

def test_duplicate_host_is_rejected():
    text = INVENTORY + "  web01: {address: 10.0.0.1}\n"
    with pytest.raises(InventoryError, match="web01"):
        parse_inventory(text)


@pytest.mark.parametrize(
    "text",
    [
        "hosts: {}",
        "hosts: {a: {ssh_user: u, ssh_key: k}}",
        "hosts: {a: {address: x}}",
        "hosts: {a: {address: x, ssh_user: u, ssh_key: k, urls: https://x/}}",
        "hosts: [",
    ],
)
def test_invalid_inventory_is_rejected(text):
    with pytest.raises(InventoryError):
        parse_inventory(text)


def test_omitted_lists_are_empty():
    hosts = parse_inventory(INVENTORY)
    stor = [h for h in hosts if h.name == "stor01"][0]
    assert stor.services == () and stor.urls == ()


def test_bad_inventory_refuses_run_before_any_probe(tmp_path, capsys):
    inv = tmp_path / "inv.yaml"
    inv.write_text(INVENTORY + "  web01: {address: 10.0.0.1}\n")
    called = []
    code = main(["run", "--inventory", str(inv)], probes=stub_probes(disk=lambda h: called.append(h)))
    assert code == 2 and called == []


# ------------------------------------------------------------ CLI, slots and lock

@pytest.fixture
def paths(tmp_path):
    inv = tmp_path / "inventory.yaml"
    inv.write_text(INVENTORY)
    return str(inv), str(tmp_path / "ops.db")


def test_store_run_commits_fifteen_rows_and_retry_is_noop(paths, capsys):
    inv, db = paths
    clock = lambda: 300 * 1000 + 5  # noqa: E731 - fixed slot 1000
    assert main(["run", "--inventory", inv, "--store", "--db", db], stub_probes(), wall_clock=clock) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["slot"] == 1000 and out["results"] == 15
    assert main(["run", "--inventory", inv, "--store", "--db", db], stub_probes(), wall_clock=clock) == 0
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM check_results").fetchone()[0] == 15
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_overlapping_run_skips_the_slot(paths, capsys):
    inv, db = paths
    called = []
    with run_lock(db + ".lock"):
        code = main(
            ["run", "--inventory", inv, "--store", "--db", db],
            stub_probes(disk=lambda h: called.append(h)),
        )
    assert code == 0 and called == []
    assert "skipped" in capsys.readouterr().err


def test_simulated_offline_host_run_through_commit_is_under_budget(paths, capsys):
    """Simulated timing only: stubbed probes, offline host modeled with a scaled 1 s deadline."""
    inv, db = paths

    def slow_for_stor01(host):
        if host.address.endswith(".13"):
            time.sleep(1.0)
            return Observation("Unknown", "ssh failed", "deadline expired")
        return Observation("OK", "fine", "stub")

    start = time.monotonic()
    probes = stub_probes(disk=slow_for_stor01, services=slow_for_stor01, uptime=slow_for_stor01)
    assert main(["run", "--inventory", inv, "--store", "--db", db], probes, process_start=start) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["seconds_from_process_start_to_commit"] < 1.5
