# Day 13 evidence: recorded incident drill (2026-11-21)

Drill: `opswatch-demo` stopped on app01 (runbook §2, a service stopped by mistake), then detected, diagnosed, fixed and auto-resolved on camera.

| File | What it shows |
|---|---|
| `01-drill-detect-diagnose.mp4` (88 s) | Status grid at slot 5984340 (23:00:00Z): app01 services **Critical `opswatch-demo inactive`**, open incident #13. Incident page: timeline from slot 5984333 (22:25Z, first Critical) to **opened at 5984334** (22:30Z, the second consecutive Critical slot); the stored evidence that opened it, the raw `systemctl` output (`@@UNIT=0 inactive`, `@@RC=3`). Trimmed from the original 2 min recording before a terminal frame that showed a client IP; compressed for the repo. |
