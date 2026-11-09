# Day 9 evidence: web UI on the live lab

Reached from the laptop through an SSH tunnel (`ssh -L 8000:127.0.0.1:8000 azureuser@<ops-ctl>`); the UI listens on ops-ctl's loopback only.

| File | What it proves |
|---|---|
| `01-status-grid.png` | Status grid for slot 5980846 (19:50:00Z, 3 min old, 1.5 s to commit), so no Stale banner. All SSH checks OK. app01/stor01 http and cert show `n/a` (no URLs configured), styled differently from OK and Unknown. web01 cert is Warning (9 days left on the 10-day lab cert) and links to open incident #1. |
| `02-incidents.png` | Incident list, open first: #1 web01/cert Warning still open; #3 app01/services Critical resolved after 20 min; #2 stor01/uptime Warning resolved after 35 min. |
| `03-incident-3-detail.png` | Incident #3 (the Day 8 drill): UTC timeline slot by slot, with `opened` on the second Critical slot (18:30:01Z) and `resolved` on the second OK slot (18:50:01Z), which is the 2-slot flapping rule. The opening evidence is the raw `systemctl` output (`inactive`, rc 3). |
| `04-incident-3-resolving-evidence.png` | The evidence that resolved it (`opswatch-demo active`, rc 0) and the empty *Resolution notes* form, before the Day 10 note was added. |
