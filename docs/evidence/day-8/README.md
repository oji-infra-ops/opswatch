# Day 8 evidence: OpsWatch on a 5-minute systemd timer (2026-11-09)

Scheduler acceptance has two parts (SPEC §6.3): the slot logic is proven by the test suite, and the systemd launch is proven on ops-ctl by `deploy/verify-timer.sh`.

| # | Screenshot | What it shows | Why it matters |
|---|------------|---------------|----------------|
| 0 | [00-units-installed.png](00-units-installed.png) | `deploy/systemd/opswatch.{service,timer}` copied to `/etc/systemd/system`, `daemon-reload`, `enable --now opswatch.timer` | Timer armed now and on every boot. |
| 1 | [01-scheduler-verified.png](01-scheduler-verified.png) | `verify-timer.sh`: next firing 18:20:00Z on a slot boundary (slot 5980828); systemd started the service at 18:20:00Z and it exited 0; journal shows `"committed": true`; slot committed exactly once with 15 results, no duplicates. `SCHEDULER VERIFIED` | systemd, not a person, runs OpsWatch on the slot, and the run lands in SQLite once. |

## Slot logic, proven in tests (74 pass)

- Late start keeps the slot it started in: `test_late_start_keeps_the_slot_it_started_in`
- Manual run during a scheduled run skips; slot written once: `test_manual_run_during_scheduled_run_skips_and_rows_stay_single`
- Missed slot breaks streaks: `test_missed_slot_breaks_opening_streak`, `test_missed_slot_breaks_recovery_but_keeps_incident_open`
- Retried slot writes nothing: `test_retrying_a_slot_writes_nothing_and_cannot_advance_a_streak`, `test_store_run_commits_fifteen_rows_and_retry_is_noop`
