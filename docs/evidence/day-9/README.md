# Day 9 evidence: web UI on the live lab

Reached from the laptop through an SSH tunnel (`ssh -L 8000:127.0.0.1:8000 azureuser@<ops-ctl>`); the UI listens on ops-ctl's loopback only.

| File | What it proves |
|---|---|
| `01-status-grid.png` | Status grid for slot 5980846 (19:50:00Z, 3 min old, 1.5 s to commit), so no Stale banner. All SSH checks OK. app01/stor01 http and cert show `n/a` (no URLs configured), styled differently from OK and Unknown. web01 cert is Warning (9 days left on the 10-day lab cert) and links to open incident #1. |
