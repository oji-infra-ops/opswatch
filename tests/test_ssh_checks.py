import time

from conftest import make_host

from opswatch.sshchecks import (
    check_disk,
    check_services,
    parse_disk,
    parse_services,
    parse_uptime,
    services_script,
)
from opswatch.sshrun import SSH_DEADLINE_SECONDS, CmdResult, run_with_deadline

HEADER = "Filesystem     1024-blocks    Used Available Capacity Mounted on"


def df(*rows: str) -> str:
    return "\n".join([HEADER, *rows]) + "\n"


# ------------------------------------------------------------ disk

def test_disk_excludes_tmpfs_and_devtmpfs_by_source_name():
    out = df(
        "tmpfs             100       99         1      99% /run",
        "devtmpfs          100       95         5      95% /dev",
        "/dev/sda1      100000    50000     50000      50% /",
    )
    obs = parse_disk(out)
    assert (obs.severity, obs.value) == ("OK", "/ 50%")


def test_disk_exclusion_is_exact_match_not_prefix():
    out = df("tmpfs2   100 95 5 95% /weird")
    assert parse_disk(out).severity == "Critical"


def test_disk_mount_path_with_spaces():
    out = df("/dev/sdb1  2000000 1840000 160000 92% /mnt/my data disk")
    obs = parse_disk(out)
    assert (obs.severity, obs.value) == ("Critical", "/mnt/my data disk 92%")


def test_disk_equal_percentages_first_row_wins():
    out = df("/dev/sda1 100 85 15 85% /", "/dev/sdb1 100 85 15 85% /data")
    assert parse_disk(out).value == "/ 85%"


def test_disk_thresholds_are_inclusive():
    assert parse_disk(df("/dev/a 100 79 21 79% /")).severity == "OK"
    assert parse_disk(df("/dev/a 100 80 20 80% /")).severity == "Warning"
    assert parse_disk(df("/dev/a 100 90 10 90% /")).severity == "Critical"


def test_disk_malformed_row_alongside_critical_mount_is_unknown():
    out = df("/dev/sdb1 100 95 5 95% /data", "/dev/sdc1 100 garbage")
    obs = parse_disk(out)
    assert obs.severity == "Unknown"
    assert "/dev/sdc1 100 garbage" in obs.evidence


def test_disk_non_integer_capacity_is_unknown():
    assert parse_disk(df("/dev/a 100 50 50 50.5% /")).severity == "Unknown"


def test_disk_wrong_header_is_unknown():
    assert parse_disk("Sys 1K-blocks Used Avail Use% Mounted on\n/dev/a 1 1 0 99% /\n").severity == "Unknown"


def test_disk_only_excluded_rows_is_unknown():
    assert parse_disk(df("tmpfs 100 1 99 1% /run")).severity == "Unknown"


def test_disk_uses_c_locale_and_ssh_failure_is_unknown():
    calls = []

    def fake_run(argv, timeout):
        calls.append((argv, timeout))
        return CmdResult(255, "", "ssh: connect to host 192.0.2.10 port 22: No route to host")

    obs = check_disk(make_host(), run=fake_run)
    assert obs.severity == "Unknown" and "No route" in obs.evidence
    assert calls[0][0][-1] == "LC_ALL=C df -P"
    assert calls[0][1] == SSH_DEADLINE_SECONDS


# ------------------------------------------------------------ services

def svc_out(failed_lines=(), failed_rc=0, units=()):
    lines = ["@@FAILED", *failed_lines, f"@@RC={failed_rc}"]
    for i, (state, rc) in enumerate(units):
        lines += [f"@@UNIT={i}", *([state] if state is not None else []), f"@@RC={rc}"]
    return "\n".join(lines + ["@@END"]) + "\n"


def test_services_all_good():
    obs = parse_services(svc_out(units=[("active", 0)]), "", ("opswatch-demo",))
    assert (obs.severity, obs.value) == ("OK", "no failed units")


def test_services_stopped_unit_is_critical_despite_nonzero_exit():
    obs = parse_services(svc_out(units=[("inactive", 3)]), "", ("opswatch-demo",))
    assert (obs.severity, obs.value) == ("Critical", "opswatch-demo inactive")


def test_services_is_active_output_is_trimmed():
    obs = parse_services(svc_out(units=[("  active  ", 0)]), "", ("nginx",))
    assert obs.severity == "OK"


def test_services_failed_unit_plus_empty_output_is_critical():
    out = svc_out(failed_lines=["foo.service loaded failed failed Foo"], units=[(None, 4)])
    obs = parse_services(out, "Failed to connect to bus", ("opswatch-demo",))
    assert obs.severity == "Critical"
    assert "failed: foo.service" in obs.value
    assert "Failed to connect to bus" in obs.evidence  # stderr kept for diagnosis


def test_services_empty_is_active_output_alone_is_unknown():
    obs = parse_services(svc_out(units=[(None, 4)]), "", ("opswatch-demo",))
    assert obs.severity == "Unknown"


def test_services_failed_query_error_is_unknown():
    obs = parse_services(svc_out(failed_rc=1), "", ())
    assert obs.severity == "Unknown"


def test_services_failed_query_lists_all_unit_types():
    out = svc_out(failed_lines=["backup.timer loaded failed failed Backup", "data.mount loaded failed failed x"])
    obs = parse_services(out, "", ())
    assert obs.value == "failed: backup.timer, data.mount"


def test_services_truncated_output_is_unknown():
    assert parse_services("@@FAILED\n@@RC=0\n", "", ()).severity == "Unknown"


def test_services_one_session_shares_one_deadline():
    calls = []

    def fake_run(argv, timeout):
        calls.append((argv, timeout))
        return CmdResult(0, svc_out(units=[("active", 0), ("active", 0)]), "")

    host = make_host(services=["nginx", "opswatch-demo"])
    obs = check_services(host, run=fake_run)
    assert obs.severity == "OK"
    assert len(calls) == 1, "both parts must run in one SSH session"
    assert calls[0][1] == SSH_DEADLINE_SECONDS
    script = calls[0][0][-1]
    assert script == services_script(("nginx", "opswatch-demo"))
    assert "systemctl --failed" in script and script.count("is-active") == 2


def test_services_ssh_timeout_makes_whole_check_unknown():
    def fake_run(argv, timeout):
        return CmdResult(None, "@@FAILED\n", "", timed_out=True)

    assert check_services(make_host(services=["x"]), run=fake_run).severity == "Unknown"


# ------------------------------------------------------------ uptime

def test_uptime_boundary():
    assert parse_uptime("3599.99 100.0").severity == "Warning"
    assert parse_uptime("3600.00 100.0").severity == "OK"


def test_uptime_invalid():
    for text in ("", "abc 1", "-5 1", "nan 1", "inf 1"):
        assert parse_uptime(text).severity == "Unknown", text


# ------------------------------------------------------------ deadline runner

def test_run_with_deadline_kills_process_group():
    t0 = time.monotonic()
    res = run_with_deadline(["sh", "-c", "sleep 30 & sleep 30"], 0.5)
    assert res.timed_out and res.transport_failed
    assert time.monotonic() - t0 < 3
