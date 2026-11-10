"""Web UI and reports (Days 9–10): status grid, Unknown vs OK, stale data, incidents, notes, reports."""

from datetime import datetime, timezone

import pytest
from conftest import LAB
from fastapi.testclient import TestClient

from opswatch.models import CHECKS, Result, utc_iso
from opswatch.store import commit_run, connect
from opswatch.web import create_app

BASE = 5980828  # 2026-11-09T18:20:00Z


def slot_time(slot: int, plus: int = 2) -> str:
    return utc_iso(datetime.fromtimestamp(slot * 300 + plus, timezone.utc))


def full_run(conn, slot, overrides=None):
    """15 results like the lab: web01 has URLs, app01/stor01 http+cert are not applicable."""
    overrides = overrides or {}
    results = []
    for h in LAB:
        for c in CHECKS:
            if c in ("http", "cert") and h.name != "web01":
                results.append(Result(h.name, c, "Unknown", False, "Not applicable", "no urls configured for host",
                                      slot_time(slot)))
                continue
            sev, val = overrides.get((h.name, c), ("OK", f"{c} fine"))
            results.append(Result(h.name, c, sev, True, val, f"raw output for {h.name}/{c}: {val}", slot_time(slot)))
    commit_run(conn, slot, results, LAB, slot_time(slot, 0), 1.6)


@pytest.fixture
def lab_db(tmp_path):
    """app01/services: OK, Critical x2, OK x2 (incident opens and resolves); stor01 offline in the last slot."""
    path = str(tmp_path / "opswatch.db")
    conn = connect(path)
    down = {("app01", "services"): ("Critical", "opswatch-demo inactive")}
    full_run(conn, BASE)
    full_run(conn, BASE + 1, down)
    full_run(conn, BASE + 2, down)
    full_run(conn, BASE + 3)
    full_run(conn, BASE + 4)
    offline = {("stor01", c): ("Unknown", "ssh failed") for c in ("disk", "services", "uptime")}
    full_run(conn, BASE + 5, offline)
    conn.close()
    return path


def client(path, minutes_after_last_run=1):
    now = datetime.fromtimestamp((BASE + 5) * 300 + minutes_after_last_run * 60, timezone.utc)
    return TestClient(create_app(path, clock=lambda: now))


def test_status_grid_shows_latest_slot_and_unknown_differs_from_ok(lab_db):
    page = client(lab_db).get("/")
    assert page.status_code == 200
    html = page.text
    assert f"slot {BASE + 5}" in html
    assert 'class="sev Unknown">Unknown' in html and "ssh failed" in html
    assert 'class="sev OK">OK' in html
    assert 'class="sev NA">n/a' in html  # not applicable is its own look, not Unknown and not OK
    assert ".Unknown::before" in html  # the "?" marker in the stylesheet
    assert "Stale" not in html


def test_status_marks_old_results_stale(lab_db):
    html = client(lab_db, minutes_after_last_run=11).get("/").text
    assert "Stale:" in html and "treat every host as Unknown" in html


def test_status_with_no_runs_says_nothing_is_known(tmp_path):
    html = TestClient(create_app(str(tmp_path / "empty.db"))).get("/").text
    assert "No runs recorded yet" in html


def test_inventory_lists_hosts_with_last_check(lab_db):
    html = client(lab_db).get("/inventory").text
    for h in ("web01", "app01", "stor01", "192.0.2.10"):
        assert h in html
    assert slot_time(BASE + 5) in html


def test_incident_list_open_first_and_filter(lab_db):
    c = client(lab_db)
    html = c.get("/incidents").text
    assert "app01 / services" in html and "resolved" in html
    assert "app01 / services" not in c.get("/incidents?host=web01").text


def test_incident_detail_has_utc_timeline_and_evidence(lab_db):
    html = client(lab_db).get("/incidents/1").text
    assert "Timeline (UTC)" in html
    assert slot_time(BASE + 1) in html and slot_time(BASE + 4) in html
    assert "opswatch-demo inactive" in html
    assert "Evidence that opened it" in html and "Evidence that resolved it" in html
    assert ">opened<" in html and ">resolved<" in html


def test_add_note_is_the_only_write_and_is_escaped(lab_db):
    c = client(lab_db)
    r = c.post("/incidents/1/notes", content="note=Restarted+%3Cb%3Eopswatch-demo%3C%2Fb%3E",
               headers={"content-type": "application/x-www-form-urlencoded"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/incidents/1#notes"
    html = c.get("/incidents/1").text
    assert "Restarted &lt;b&gt;opswatch-demo&lt;/b&gt;" in html
    assert c.post("/incidents/1/notes", content="note=++",
                  headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 400
    assert c.post("/incidents/99/notes", content="note=x",
                  headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 404


def test_incident_report_has_timestamps_evidence_and_notes(lab_db):
    c = client(lab_db)
    c.post("/incidents/1/notes", content="note=demo+service+stopped+for+the+drill",
           headers={"content-type": "application/x-www-form-urlencoded"})
    r = c.get("/incidents/1/report.md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "attachment" in r.headers["content-disposition"]
    md = r.text
    assert "# Incident 1: app01 / services" in md
    assert slot_time(BASE + 2) in md and slot_time(BASE + 4) in md  # opened, resolved
    assert "raw output for app01/services: opswatch-demo inactive" in md
    assert "raw output for app01/services: services fine" in md  # resolving evidence
    assert "demo service stopped for the drill" in md
    assert "| Duration | 10 min |" in md


def test_summary_report_counts_and_mean_time_to_resolve(lab_db):
    md = client(lab_db).get("/reports/summary.md?from=2026-11-09&to=2026-11-09").text
    assert "6 runs committed; 1 incidents opened; 1 resolved; 0 still open" in md
    assert "Mean time to resolve: 10 min" in md
    assert "| app01 | services | 1 | 1 | Critical |" in md
    assert "2026-11-09T18:30:02Z" in md  # every report carries timestamps


def test_summary_report_rejects_bad_dates(lab_db):
    c = client(lab_db)
    assert c.get("/reports/summary.md?from=yesterday&to=2026-11-09").status_code == 400
    assert c.get("/reports/summary.md?from=2026-11-10&to=2026-11-09").status_code == 400


def test_missing_incident_is_404(lab_db):
    assert client(lab_db).get("/incidents/42").status_code == 404
    assert client(lab_db).get("/incidents/42/report.md").status_code == 404
