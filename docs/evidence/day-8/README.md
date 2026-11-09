# Day 8 evidence: OpsWatch on a 5-minute systemd timer (2026-11-09)

Scheduler acceptance has two parts (SPEC §6.3): the slot logic is proven by the test suite, and the systemd launch is proven on ops-ctl by `deploy/verify-timer.sh`.

| # | Screenshot | What it shows | Why it matters |
|---|------------|---------------|----------------|
| 0 | [00-units-installed.png](00-units-installed.png) | `deploy/systemd/opswatch.{service,timer}` copied to `/etc/systemd/system`, `daemon-reload`, `enable --now opswatch.timer` | Timer armed now and on every boot. |
| 1 | [01-scheduler-verified.png](01-scheduler-verified.png) | `verify-timer.sh`: next firing 18:20:00Z on a slot boundary (slot 5980828); systemd started the service at 18:20:00Z and it exited 0; journal shows `"committed": true`; slot committed exactly once with 15 results, no duplicates. `SCHEDULER VERIFIED` | systemd, not a person, runs OpsWatch on the slot, and the run lands in SQLite once. |

| 2 | [02-incident-opened.png](02-incident-opened.png) | Drill: `opswatch-demo` stopped on app01 after the 18:20 run. app01/services: OK at 5980828, Critical `opswatch-demo inactive` at 5980829, 5980830, 5980831. Incident #3 app01/services opened Critical at **5980830**, the second consecutive Critical slot | A stopped (inactive, not failed) unit is caught by the per-unit `is-active` check, and the incident opens on the second bad slot, not the first. Also open: #1 web01/cert Warning (10-day cert) and #2 stor01/uptime Warning (stor01 was rebooted for the Day 2 outage test, uptime < 1 h for two slots). |
| 3 | [03-incident-resolved.png](03-incident-resolved.png) | `opswatch-demo` started again after the 18:40 run. app01/services: Critical at 5980831 and 5980832, OK at 5980833, 5980834, 5980835. Incident #3 **resolved at 5980834**, the second consecutive OK slot. #2 stor01/uptime resolved at 5980835 once uptime passed 1 h. #1 web01/cert stays open. Runs table shows timer runs at 18:15:00Z, 18:20:00Z, 18:25:00Z | Full incident lifecycle with no human in the loop: break, detect, open on the 2nd bad slot, fix, resolve on the 2nd good slot. The 18:15 run explains why #2 could open at 5980828 (two consecutive slots, 827 and 828). |

## Slot logic, proven in tests (74 pass)

- Late start keeps the slot it started in: `test_late_start_keeps_the_slot_it_started_in`
- Manual run during a scheduled run skips; slot written once: `test_manual_run_during_scheduled_run_skips_and_rows_stay_single`
- Missed slot breaks streaks: `test_missed_slot_breaks_opening_streak`, `test_missed_slot_breaks_recovery_but_keeps_incident_open`
- Retried slot writes nothing: `test_retrying_a_slot_writes_nothing_and_cannot_advance_a_streak`, `test_store_run_commits_fifteen_rows_and_retry_is_noop`
