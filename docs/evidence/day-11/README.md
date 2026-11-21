# Day 11 evidence: walking the runbook on the live lab

| File | What it proves |
|---|---|
| `01-runbook-s6-vm-deallocated.png` | RUNBOOK §6 step 3 (read-only, Cloud Shell): `az vm get-instance-view` reports stor01 `VM deallocated`, confirming the cause before OpsWatch's `ssh failed` Unknowns are explained. |
| `02-runbook-s6-ssh-no-route.png` | RUNBOOK §6 step 1 (read-only, ops-ctl): the exact OpsWatch login fails fast with `No route to host`, exit 255, not a 10 s timeout. Same subnet, VM off: nothing answers ARP. Runbook updated to say what each error means. |
| `03-scheduler-verified-after-restart.png` | 2026-11-21, lab powered back on after 11 days deallocated. `verify-timer.sh` on ops-ctl: timer came back enabled on boot with nobody re-arming it, systemd fired at 19:50:00Z (slot 5984302), exit 0, slot committed once with 15 results and no duplicates. `SCHEDULER VERIFIED`. All 15 results means ops-ctl reached web01, app01 and stor01. |
