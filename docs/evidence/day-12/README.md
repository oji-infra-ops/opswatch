# Day 12 evidence: deploy as code

| File | What it proves |
|---|---|
| `01-deploy-as-code.png` | `ansible-playbook lab/ansible/site.yml` deploys OpsWatch itself: code to `/opt/opswatch`, data to `/var/lib/opswatch`, timer and web UI units installed and enabled. PLAY RECAP: ops-ctl `changed=8` (7 tasks plus the web UI restart handler), web01/app01/stor01 `changed=0`, every host `failed=0`. |
| `02-scheduler-verified-after-deploy.png` | `verify-timer.sh` against the deployed layout (`/opt/opswatch`, `/var/lib/opswatch/opswatch.db`): systemd started the run at 19:50:00Z, it exited 0, and slot 5980846 was committed once with 15 results and no duplicates. `SCHEDULER VERIFIED`. |
