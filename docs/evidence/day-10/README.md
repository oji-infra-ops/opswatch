# Day 10 evidence: resolution notes and reports

| File | What it proves |
|---|---|
| `01-resolution-note.png` | A resolution note added to incident #3 through the web UI (its only write), stamped `[2026-11-09T20:43:30Z]` and shown under *Resolution notes*, saying what the human did during the Day 8 drill. |
| `03-incident-3-report-end.png` | End of incident #3's Markdown report (`GET /incidents/3/report.md`, the same file the *Download report* button serves), fetched on ops-ctl with curl: the resolving evidence and the resolution note are in the report. |
| `02-incident-3-report-start.png` | Start of the same report: status, opening and final severity, opened 18:30:01Z (slot 5980830), resolved 18:50:01Z (slot 5980834), duration 20 min, the slot-by-slot UTC timeline and the opened/resolved events. |
