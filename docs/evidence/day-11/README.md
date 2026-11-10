# Day 11 evidence: walking the runbook on the live lab

| File | What it proves |
|---|---|
| `01-runbook-s6-vm-deallocated.png` | RUNBOOK §6 step 3 (read-only, Cloud Shell): `az vm get-instance-view` reports stor01 `VM deallocated`, confirming the cause before OpsWatch's `ssh failed` Unknowns are explained. |
| `02-runbook-s6-ssh-no-route.png` | RUNBOOK §6 step 1 (read-only, ops-ctl): the exact OpsWatch login fails fast with `No route to host`, exit 255, not a 10 s timeout. Same subnet, VM off: nothing answers ARP. Runbook updated to say what each error means. |
