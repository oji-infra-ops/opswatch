"""Read-mostly web UI over the OpsWatch database (Days 9–10).

Pages: status grid, inventory, incidents list and detail, Markdown reports. The only write is
appending a resolution note to an incident. No login: bind to 127.0.0.1 and reach it through an
SSH tunnel (lab/azure/README.md). Run with:
    OPSWATCH_DB=~/opswatch.db uvicorn opswatch.web:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .models import CHECKS, NOT_APPLICABLE, RANK
from .schedule import SLOT_SECONDS
from .store import connect

STALE_AFTER_SECONDS = 2 * SLOT_SECONDS  # two missed slots: the grid is no longer current
NOTE_MAX_CHARS = 2000

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _default_db() -> str:
    return os.path.expanduser(os.environ.get("OPSWATCH_DB", "~/opswatch.db"))


def parse_iso(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def duration(start: str, end: str | None) -> str:
    if not end:
        return ""
    secs = int((parse_iso(end) - parse_iso(start)).total_seconds())
    h, rem = divmod(secs, 3600)
    return f"{h} h {rem // 60} min" if h else f"{rem // 60} min"


# ------------------------------------------------------------------ queries

def latest_run(conn: sqlite3.Connection):
    return conn.execute("SELECT * FROM runs ORDER BY slot DESC LIMIT 1").fetchone()


def status_grid(conn: sqlite3.Connection):
    run = latest_run(conn)
    hosts = conn.execute("SELECT * FROM hosts ORDER BY name").fetchall()
    cells = {}
    if run:
        for r in conn.execute("SELECT * FROM check_results WHERE slot = ?", (run["slot"],)):
            cells[(r["host"], r["check_name"])] = r
    open_keys = {
        (i["host"], i["check_name"]): i["id"]
        for i in conn.execute("SELECT id, host, check_name FROM incidents WHERE status = 'open'")
    }
    return run, hosts, cells, open_keys


def incidents(conn: sqlite3.Connection, host: str | None = None):
    sql = "SELECT * FROM incidents"
    args: tuple = ()
    if host:
        sql += " WHERE host = ?"
        args = (host,)
    sql += " ORDER BY status = 'open' DESC, opened_slot DESC, id DESC"
    return conn.execute(sql, args).fetchall()


def incident(conn: sqlite3.Connection, incident_id: int):
    row = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="no such incident")
    events = conn.execute(
        "SELECT e.*, r.value, r.evidence FROM incident_events e JOIN check_results r ON r.id = e.result_id "
        "WHERE e.incident_id = ? ORDER BY e.slot, e.id",
        (incident_id,),
    ).fetchall()
    results = conn.execute(
        "SELECT slot, timestamp, severity, value FROM check_results "
        "WHERE host = ? AND check_name = ? AND slot BETWEEN ? AND ? ORDER BY slot",
        (row["host"], row["check_name"], row["opened_slot"] - 1, row["resolved_slot"] or 2**62),
    ).fetchall()
    return row, events, results


def add_note(conn: sqlite3.Connection, incident_id: int, text: str, now: str) -> None:
    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="note is empty")
    if len(text) > NOTE_MAX_CHARS:
        raise HTTPException(status_code=400, detail=f"note is longer than {NOTE_MAX_CHARS} characters")
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT resolution_notes FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no such incident")
        entry = f"[{now}] {text}"
        notes = f"{row['resolution_notes']}\n{entry}" if row["resolution_notes"] else entry
        conn.execute("UPDATE incidents SET resolution_notes = ? WHERE id = ?", (notes, incident_id))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


# ------------------------------------------------------------------ reports

def incident_report(row, events, results) -> str:
    lines = [
        f"# Incident {row['id']}: {row['host']} / {row['check_name']}",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Status | {row['status']} |",
        f"| Opening severity | {row['opening_severity']} |",
        f"| Current or final severity | {row['severity']} |",
        f"| Opened (UTC) | {row['opened_at']} (slot {row['opened_slot']}) |",
        f"| Resolved (UTC) | {row['resolved_at'] or 'still open'}"
        + (f" (slot {row['resolved_slot']})" if row["resolved_slot"] else "") + " |",
        f"| Duration | {duration(row['opened_at'], row['resolved_at']) or 'still open'} |",
        "",
        "## Timeline (UTC)",
        "",
        "| Slot | Time | Severity | Value |",
        "|---|---|---|---|",
    ]
    lines += [f"| {r['slot']} | {r['timestamp']} | {r['severity']} | {r['value']} |" for r in results]
    lines += ["", "## Incident events", ""]
    lines += [f"- {e['timestamp']} slot {e['slot']}: **{e['kind']}** ({e['severity']}) {e['value']}" for e in events]
    lines += ["", "## Evidence that opened it", "", "```", row["opening_evidence"], "```", ""]
    for e in events:
        if e["kind"] == "resolved":
            lines += ["## Evidence that resolved it", "", "```", e["evidence"], "```", ""]
    lines += ["## Resolution notes", "", row["resolution_notes"] or "_none yet_", ""]
    return "\n".join(lines)


def summary_report(conn: sqlite3.Connection, start: str, end: str) -> str:
    """Incidents opened in [start, end), dates as YYYY-MM-DD (UTC)."""
    try:
        t0 = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        t1 = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="from and to must be YYYY-MM-DD") from exc
    if t1 <= t0:
        raise HTTPException(status_code=400, detail="to must not be before from")
    lo, hi = t0.strftime("%Y-%m-%dT%H:%M:%SZ"), t1.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        "SELECT * FROM incidents WHERE opened_at >= ? AND opened_at < ? ORDER BY opened_at", (lo, hi)
    ).fetchall()
    runs = conn.execute(
        "SELECT COUNT(*) AS n FROM runs WHERE started_at >= ? AND started_at < ?", (lo, hi)
    ).fetchone()["n"]

    counts: dict[tuple[str, str], list] = {}
    for r in rows:
        counts.setdefault((r["host"], r["check_name"]), []).append(r)
    resolved = [r for r in rows if r["resolved_at"]]
    mttr = (
        sum((parse_iso(r["resolved_at"]) - parse_iso(r["opened_at"])).total_seconds() for r in resolved)
        / len(resolved) if resolved else None
    )

    lines = [
        f"# OpsWatch summary, {start} to {end} (UTC)",
        "",
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}. "
        f"{runs} runs committed; {len(rows)} incidents opened; {len(resolved)} resolved; "
        f"{len(rows) - len(resolved)} still open.",
        "",
        f"Mean time to resolve: {round(mttr / 60)} min" if mttr is not None else "Mean time to resolve: none resolved",
        "",
        "## Incidents by host and check",
        "",
        "| Host | Check | Opened | Resolved | Worst severity |",
        "|---|---|---|---|---|",
    ]
    for (host, check), group in sorted(counts.items()):
        worst = max((g["severity"] for g in group), key=RANK.__getitem__)
        lines.append(f"| {host} | {check} | {len(group)} | {sum(1 for g in group if g['resolved_at'])} | {worst} |")
    lines += ["", "## Incidents", "",
              "| ID | Host / check | Opened (UTC) | Resolved (UTC) | Duration |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r['id']} | {r['host']} / {r['check_name']} | {r['opened_at']} | {r['resolved_at'] or 'open'} "
            f"| {duration(r['opened_at'], r['resolved_at']) or 'open'} |"
        )
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ app

def create_app(db_path: str | None = None, clock=lambda: datetime.now(timezone.utc)) -> FastAPI:
    app = FastAPI(title="OpsWatch", docs_url=None, redoc_url=None)
    path = db_path or _default_db()

    def db() -> sqlite3.Connection:
        return connect(path)

    def now_iso() -> str:
        return clock().strftime("%Y-%m-%dT%H:%M:%SZ")

    def page(request: Request, name: str, **ctx) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(request, name, {"checks": CHECKS, "NA": NOT_APPLICABLE, **ctx})

    @app.get("/", response_class=HTMLResponse)
    def status(request: Request):
        conn = db()
        try:
            run, hosts, cells, open_keys = status_grid(conn)
        finally:
            conn.close()
        age = (clock() - parse_iso(run["started_at"])).total_seconds() if run else None
        return page(request, "status.html", run=run, hosts=hosts, cells=cells, open_keys=open_keys,
                    age_min=None if age is None else int(age // 60),
                    stale=age is None or age > STALE_AFTER_SECONDS)

    @app.get("/inventory", response_class=HTMLResponse)
    def inventory(request: Request):
        conn = db()
        try:
            rows = conn.execute(
                "SELECT h.*, MAX(r.timestamp) AS last_check FROM hosts h "
                "LEFT JOIN check_results r ON r.host = h.name GROUP BY h.name ORDER BY h.name"
            ).fetchall()
        finally:
            conn.close()
        return page(request, "inventory.html", hosts=rows)

    @app.get("/incidents", response_class=HTMLResponse)
    def incident_list(request: Request, host: str | None = None):
        conn = db()
        try:
            rows = incidents(conn, host or None)
            hosts = [r["name"] for r in conn.execute("SELECT name FROM hosts ORDER BY name")]
        finally:
            conn.close()
        return page(request, "incidents.html", incidents=rows, hosts=hosts, host=host or "", duration=duration)

    @app.get("/incidents/{incident_id}", response_class=HTMLResponse)
    def incident_detail(request: Request, incident_id: int):
        conn = db()
        try:
            row, events, results = incident(conn, incident_id)
        finally:
            conn.close()
        return page(request, "incident.html", i=row, events=events, results=results,
                    duration=duration(row["opened_at"], row["resolved_at"]), note_max=NOTE_MAX_CHARS)

    @app.post("/incidents/{incident_id}/notes")
    async def incident_note(incident_id: int, request: Request):
        form = parse_qs((await request.body()).decode("utf-8", "replace"))
        conn = db()
        try:
            add_note(conn, incident_id, (form.get("note") or [""])[0], now_iso())
        finally:
            conn.close()
        return RedirectResponse(f"/incidents/{incident_id}#notes", status_code=303)

    @app.get("/incidents/{incident_id}/report.md", response_class=PlainTextResponse)
    def incident_md(incident_id: int):
        conn = db()
        try:
            text = incident_report(*incident(conn, incident_id))
        finally:
            conn.close()
        return PlainTextResponse(text, media_type="text/markdown; charset=utf-8", headers={
            "Content-Disposition": f'attachment; filename="opswatch-incident-{incident_id}.md"'})

    @app.get("/reports/summary.md", response_class=PlainTextResponse)
    def summary_md(request: Request):
        today = clock().strftime("%Y-%m-%d")
        start = request.query_params.get("from") or (clock() - timedelta(days=7)).strftime("%Y-%m-%d")
        end = request.query_params.get("to") or today
        conn = db()
        try:
            text = summary_report(conn, start, end)
        finally:
            conn.close()
        return PlainTextResponse(text, media_type="text/markdown; charset=utf-8", headers={
            "Content-Disposition": f'attachment; filename="opswatch-summary-{start}-to-{end}.md"'})

    return app


app = create_app()
