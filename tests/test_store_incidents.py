import pytest
from conftest import LAB, result

from opswatch import store
from opswatch.store import RunWriteError, commit_run


def commit(db, slot, *results):
    return commit_run(db, slot, list(results), LAB, "2026-11-09T00:00:00Z", 1.0)


def incidents(db):
    return [dict(r) for r in db.execute("SELECT * FROM incidents ORDER BY id")]


def run_sequence(db, severities, start=100, check="disk"):
    for i, sev in enumerate(severities):
        commit(db, start + i, result(sev, check=check, value=f"v{i}"))


def test_two_consecutive_non_ok_open_once_and_two_ok_resolve(db):
    run_sequence(db, ["OK", "Warning", "Warning", "Critical", "OK", "OK"])
    (inc,) = incidents(db)
    assert inc["status"] == "resolved"
    assert inc["opened_slot"] == 102 and inc["opening_severity"] == "Warning"
    assert inc["severity"] == "Critical"
    assert inc["resolved_slot"] == 105
    kinds = [r["kind"] for r in db.execute("SELECT kind FROM incident_events ORDER BY id")]
    assert kinds == ["opened", "severity_changed", "resolved"]


def test_single_blip_opens_nothing(db):
    run_sequence(db, ["OK", "Warning", "OK", "Critical", "OK"])
    assert incidents(db) == []


def test_mixed_non_ok_streak_opens_at_higher_severity(db):
    run_sequence(db, ["Unknown", "Critical"])
    assert incidents(db)[0]["opening_severity"] == "Critical"
    run_sequence(db, ["OK", "OK", "Warning", "Unknown"], start=200)
    assert incidents(db)[1]["opening_severity"] == "Unknown"  # Unknown outranks Warning


def test_changing_failure_targets_count_toward_same_incident(db):
    commit(db, 1, result("Critical", value="/data 95%"))
    commit(db, 2, result("Warning", value="/ 85%"))
    assert len(incidents(db)) == 1


def test_missed_slot_breaks_opening_streak(db):
    commit(db, 10, result("Critical"))
    commit(db, 12, result("Critical"))  # slot 11 missed
    assert incidents(db) == []
    commit(db, 13, result("Critical"))
    assert incidents(db)[0]["opened_slot"] == 13


def test_missed_slot_breaks_recovery_but_keeps_incident_open(db):
    run_sequence(db, ["Critical", "Critical", "OK"], start=20)
    commit(db, 24, result("OK"))  # slot 23 missed: recovery streak restarts
    assert incidents(db)[0]["status"] == "open"
    commit(db, 25, result("OK"))
    assert incidents(db)[0]["status"] == "resolved"


def test_inapplicable_breaks_streak_and_never_resolves(db):
    commit(db, 1, result("Unknown", check="http"))
    commit(db, 2, result("Unknown", check="http", applicable=False, value="Not applicable"))
    commit(db, 3, result("Unknown", check="http"))
    assert incidents(db) == []
    commit(db, 4, result("Unknown", check="http"))
    assert incidents(db)[0]["status"] == "open"
    commit(db, 5, result("OK", check="http"))
    commit(db, 6, result("Unknown", check="http", applicable=False, value="Not applicable"))
    commit(db, 7, result("OK", check="http"))
    assert incidents(db)[0]["status"] == "open"


def test_opening_and_recovery_results_are_linked_and_evidence_kept(db):
    run_sequence(db, ["Critical", "Warning", "Critical", "OK", "OK"], start=1)
    inc = incidents(db)[0]
    ids = {r["slot"]: r["id"] for r in db.execute("SELECT id, slot FROM check_results")}
    assert (inc["open_result_1"], inc["open_result_2"]) == (ids[1], ids[2])
    assert (inc["resolve_result_1"], inc["resolve_result_2"]) == (ids[4], ids[5])
    assert "[slot 1 " in inc["opening_evidence"] and "[slot 2 " in inc["opening_evidence"]
    linked = db.execute("SELECT COUNT(*) FROM check_results WHERE incident_id = ?", (inc["id"],))
    assert linked.fetchone()[0] == 5  # opening, intervening and recovery results


def test_retrying_a_slot_writes_nothing_and_cannot_advance_a_streak(db):
    assert commit(db, 1, result("Critical")).committed
    assert not commit(db, 1, result("Critical")).committed
    assert incidents(db) == []
    assert db.execute("SELECT COUNT(*) FROM check_results").fetchone()[0] == 1


def test_write_failure_rolls_back_everything(db, monkeypatch):
    commit(db, 1, result("Critical"))
    real = store._evaluate
    calls = {"n": 0}

    def flaky(conn, slot, rid, r):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return real(conn, slot, rid, r)

    monkeypatch.setattr(store, "_evaluate", flaky)
    with pytest.raises(RunWriteError):
        commit(db, 2, result("Critical"), result("Critical", host="web01"))
    assert incidents(db) == []
    assert db.execute("SELECT COUNT(*) FROM check_results WHERE slot = 2").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM runs WHERE slot = 2").fetchone()[0] == 0
    # The slot can be retried once the problem is fixed.
    monkeypatch.setattr(store, "_evaluate", real)
    commit(db, 2, result("Critical"), result("Critical", host="web01"))
    assert len(incidents(db)) == 1


def test_retention_prunes_old_results_but_keeps_incident_evidence(db):
    run_sequence(db, ["OK", "Critical", "Critical", "OK", "OK", "OK"], start=100)  # incident 101..104
    later = 100 + store.RETENTION_SLOTS + 10
    outcome = commit(db, later, result("OK"))
    kept = {r["slot"] for r in db.execute("SELECT slot FROM check_results")}
    # slots 100 and 105 are plain OKs older than 30 days: pruned. 101, 102 open it; 103, 104 resolve it.
    assert kept == {101, 102, 103, 104, later}
    assert outcome.pruned == 2
    (inc,) = incidents(db)
    assert inc["status"] == "resolved" and inc["opening_evidence"]


def test_retention_keeps_results_inside_the_window(db):
    run_sequence(db, ["OK", "OK", "OK"], start=100)
    outcome = commit(db, 100 + store.RETENTION_SLOTS, result("OK"))
    assert outcome.pruned == 0  # slot 100 is exactly 30 days old: kept
