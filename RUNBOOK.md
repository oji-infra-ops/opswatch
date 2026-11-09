# OpsWatch troubleshooting runbook

One section per alert. Each gives what the alert means, the first three commands to run, likely causes in order, the fix, and when to escalate. Examples come from real incidents on the lab (see `docs/evidence/`).

**Conventions**

- Commands are run from **ops-ctl** unless the line says otherwise. `H` is the host's address from `inventory.yaml` (web01 `10.10.1.11`, app01 `10.10.1.12`, stor01 `10.10.1.13`).
- **[read-only]** commands look and change nothing. Use them first, always.
- **[CHANGES HOST]** commands change a machine. Run them only after the read-only steps point at the cause, and write what you did in the incident's resolution notes (web UI → incident → *Add a note*).
- Read-only checks run as the `opswatch` user with the monitoring key, exactly as OpsWatch does: `ssh -i ~/.ssh/opswatch -o IdentitiesOnly=yes opswatch@H '<command>'`. Fixes need the admin account: `ssh azureuser@H 'sudo <command>'`.
- Severity reminder: **Unknown means OpsWatch could not see**, never "probably fine". Treat it as seriously as Critical until you know why.

---

## 0. Before anything: is OpsWatch itself current?

**Symptom:** the status page shows the purple **Stale** banner, or every check on every host is Unknown.

First three commands:

1. **[read-only]** `systemctl list-timers opswatch.timer` (is a next run scheduled?)
2. **[read-only]** `journalctl -u opswatch.service -n 30 --no-pager` (did the last runs exit 0? look for `"committed": true`)
3. **[read-only]** `sqlite3 /var/lib/opswatch/opswatch.db "SELECT slot, started_at, elapsed_seconds FROM runs ORDER BY slot DESC LIMIT 5;"`

Likely causes, in order:

1. ops-ctl was off or deallocated (auto-shutdown at 23:00 Pacific). Missed slots are not replayed; streaks restart.
2. Timer disabled after a manual change.
3. Inventory rejected (exit code 2 in the journal: `inventory rejected, nothing run`).
4. Database not writable (exit code 1: `run-level error`).

Fix:

- **[CHANGES HOST]** `sudo systemctl enable --now opswatch.timer`, then `bash /opt/opswatch/deploy/verify-timer.sh` until it prints `SCHEDULER VERIFIED`.
- Inventory errors: fix `inventory.yaml` in the repo and redeploy with the playbook. Never hand-edit `/opt/opswatch`.

Escalate if runs keep exiting non-zero after one redeploy.

---

## 1. disk: Warning (≥ 80%) or Critical (≥ 90%)

**What it means:** the fullest filesystem on the host (from `df -P`, tmpfs excluded) crossed a threshold. `value` names it, e.g. `/data 92%`.

First three commands:

1. **[read-only]** `ssh … opswatch@H 'LC_ALL=C df -P'` (confirm which mount, and that it matches the alert)
2. **[read-only]** `ssh … opswatch@H 'du -xh --max-depth=2 /data 2>/dev/null | sort -h | tail -15'` (biggest directories; use the alerting mount instead of `/data`. Paths `opswatch` can't read are skipped.)
3. **[read-only]** `ssh azureuser@H 'sudo lsof +L1 2>/dev/null | head'` (deleted files still held open: space that `du` can't see)

Likely causes, in order:

1. Logs or dumps growing in one directory.
2. A deleted-but-open file (step 3 shows it; `df` and `du` disagree).
3. Real data growth: the disk is too small.

Fix:

- **[CHANGES HOST]** remove or compress the files found in step 2 only after confirming with the owner what they are. Example from the drill: `ssh azureuser@10.10.1.13 'sudo rm /data/fill.img'`.
- **[CHANGES HOST]** for step 3, restart the process holding the file: `sudo systemctl restart <service>`.
- Real growth: resize the Azure data disk (`az disk update --size-gb`), then grow the filesystem (`sudo resize2fs`). Plan it; don't do it during the incident unless at Critical.

Escalate at Critical when you can't find what's growing within 15 minutes, or on a root (`/`) filesystem.

**Unknown on disk:** see section 6 (SSH) first. If SSH is fine, the `df` output didn't match the expected header or a row was malformed; the evidence shows the raw output.

---

## 2. services: Critical

**What it means:** either a unit is in `systemctl --failed`, or a unit listed in the host's `services` is not `active`. `value` names it, e.g. `opswatch-demo inactive`.

First three commands:

1. **[read-only]** `ssh … opswatch@H 'systemctl is-active <unit>; systemctl --failed --no-legend --plain'`
2. **[read-only]** `ssh … opswatch@H 'systemctl status <unit> --no-pager -n 20'`
3. **[read-only]** `ssh azureuser@H 'sudo journalctl -u <unit> -n 50 --no-pager'`

Likely causes, in order:

1. **Stopped by a person** (`inactive`, no errors in the journal). This is what the Day 8 drill did: `systemctl stop` leaves a unit inactive, not failed, which is why OpsWatch checks `is-active` per unit.
2. **Crashed** (`failed`; the journal shows the exit code or signal).
3. **Dependency missing** after a reboot or package change (the journal shows `Dependency failed` or a missing file).

Fix:

- **[CHANGES HOST]** `ssh azureuser@H 'sudo systemctl start <unit> && systemctl is-active <unit>'` (for failed units, fix the cause from step 3 first; restarting a crash loop hides it).
- Expect the incident to resolve **two slots later** (about 10 minutes), not at once. That's the flapping rule, not a fault.

Escalate if the unit fails again within an hour of a restart.

**Unknown on services:** SSH failed or the output lost its section markers. Section 6 first.

---

## 3. uptime: Warning (< 1 hour)

**What it means:** the host booted less than an hour ago. Nobody may have asked for that.

First three commands:

1. **[read-only]** `ssh … opswatch@H 'uptime; last -x -n 5 reboot shutdown'`
2. **[read-only]** `az vm get-instance-view -g rg-opswatch-lab -n opswatch-<host> --query "instanceView.statuses[].displayStatus" -o tsv` (Cloud Shell: was it a planned start?)
3. **[read-only]** `ssh azureuser@H 'sudo journalctl -b -1 -n 30 --no-pager'` (the end of the previous boot: clean shutdown or a crash?)

Likely causes, in order:

1. Planned: someone started or restarted it (the Day 2 outage test did exactly this to stor01).
2. Azure maintenance or auto-shutdown followed by a start.
3. Kernel panic or watchdog reset (step 3 ends abruptly, no clean shutdown).

Fix: nothing to change if it was planned; note who and why in the incident. It resolves on its own once uptime passes an hour and two OK slots follow. Check the host's other checks: services that don't start at boot show up there.

Escalate on cause 3, or two unplanned reboots in a day.

---

## 4. http: Critical or Warning

**What it means:** a URL in the host's `urls` returned 4xx/5xx, refused the connection, timed out (5 s), or failed TLS validation (Critical); or answered correctly but took more than 2 s (Warning).

First three commands:

1. **[read-only]** `curl -sS -o /dev/null -w '%{http_code} %{time_total}s\n' --cacert /etc/opswatch/web01.crt https://web01/` (same trust anchor and hostname OpsWatch uses)
2. **[read-only]** `ssh … opswatch@H 'systemctl is-active nginx; ss -ltn | grep -E ":(443|80) "'`
3. **[read-only]** `ssh azureuser@H 'sudo tail -n 30 /var/log/nginx/error.log'`

Likely causes, in order:

1. **Web server config** returns an error. The first live run found exactly this: `HTTP 403` because the site had no `index` directive and Ubuntu's page is `index.nginx-debian.html`. The error log says `directory index of "/var/www/html/" is forbidden`.
2. nginx stopped (step 2; the services check will be Critical too).
3. TLS: the certificate expired or doesn't match `web01` (curl in step 1 says so; the cert check agrees).
4. Name resolution: `web01` must resolve via `/etc/hosts` on ops-ctl.

Fix:

- Config: fix it **in `lab/ansible/site.yml`** and re-run the playbook (**[CHANGES HOST]**). Hand edits on web01 are lost on the next run.
- **[CHANGES HOST]** `ssh azureuser@H 'sudo nginx -t && sudo systemctl reload nginx'` after any config change.

Escalate on 5xx that persists across two slots with nginx running.

**Unknown on http:** a malformed URL in the inventory, or DNS / no route. The evidence names which.

---

## 5. cert: Warning (< 14 days), Critical (< 3 days or expired)

**What it means:** the leaf certificate behind an `https://` URL expires soon or has expired. `value` shows whole days left, or `expired`.

First three commands:

1. **[read-only]** `echo | openssl s_client -connect 10.10.1.11:443 -servername web01 2>/dev/null | openssl x509 -noout -subject -enddate -ext subjectAltName`
2. **[read-only]** `openssl x509 -in /etc/opswatch/web01.crt -noout -enddate` (the trust anchor ops-ctl holds)
3. **[read-only]** `ssh … opswatch@H 'ls -l /etc/ssl/certs/web01.crt'`

Likely causes, in order:

1. Expected expiry. The lab cert is valid for only 10 days on purpose, so it is **Warning from the day it's made**. This is the open incident #1 in the lab, and it is correct.
2. A renewed cert was installed on the server but nginx wasn't reloaded.
3. A new cert was installed but ops-ctl's trust anchor still has the old one: OpsWatch then reports Unknown (validation failure), not a date.

Fix:

- **[CHANGES HOST]** lab: delete the cert and re-run the playbook to make a new one and refresh the trust anchor: `ssh azureuser@10.10.1.11 'sudo rm /etc/ssl/certs/web01.crt'`, then `ansible-playbook -i lab/ansible/hosts.ini lab/ansible/site.yml`.
- Production: renew through your CA or ACME client, then `sudo systemctl reload nginx`.

Escalate at Critical if renewal is blocked.

---

## 6. Unknown with `ssh failed` (any SSH check)

**What it means:** OpsWatch could not log in and run the command within the 10-second deadline. It does **not** know whether the host is healthy.

First three commands:

1. **[read-only]** `ssh -i ~/.ssh/opswatch -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 -v opswatch@H true 2>&1 | tail -15` (the exact login OpsWatch makes, verbose)
2. **[read-only]** `ping -c 3 -W 2 H` (network reachable at all?)
3. **[read-only]** `az vm get-instance-view -g rg-opswatch-lab -n opswatch-<host> --query "instanceView.statuses[1].displayStatus" -o tsv` (Cloud Shell: running or deallocated?)

Likely causes, in order:

1. Host off or deallocated (the Day 2 acceptance test: stor01 deallocated → all three SSH checks `Unknown ssh failed`).
2. Key or user problem: `Permission denied (publickey)` in step 1 (authorized_keys changed, `opswatch` user missing after a rebuild without the playbook).
3. Slow logins close to the deadline. Seen on the lab: a forwarded ssh-agent made every check stall (9.8 s healthy run). OpsWatch now passes `IdentityAgent=none`; if checks are slow again, compare step 1's timing with and without `SSH_AUTH_SOCK=`.

Fix:

- **[CHANGES HOST]** start the VM: `az vm start -g rg-opswatch-lab -n opswatch-<host>` (Cloud Shell). Expect an uptime Warning afterwards (section 3).
- **[CHANGES HOST]** key/user problems: re-run the playbook; it restores the user and key idempotently.

Escalate if a running host stays unreachable for two slots with the network path (step 2) working.
