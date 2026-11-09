# Day 2 evidence: Azure lab built and first live run (2026-11-09)

Goal: four Ubuntu 24.04 VMs on Azure (ops-ctl, web01, app01, stor01), configured by Ansible, with read-only `opswatch` access from ops-ctl, and OpsWatch running its five checks against live hosts.

## What happened, in order

| # | Screenshot | What it shows | Why it matters |
|---|------------|---------------|----------------|
| 1 | [01-ansible-ping-permission-denied.png](01-ansible-ping-permission-denied.png) | `ansible all -m ping`: ops-ctl OK, web01/app01/stor01 `Permission denied (publickey)` | Pre-flight test caught a broken login before the playbook ran. |
| 2 | [02-single-ssh-permission-denied.png](02-single-ssh-permission-denied.png) | One manual `ssh azureuser@10.10.1.11` also denied | Ruled out Ansible's parallel connections. All VMs share one admin key that works on ops-ctl, so the fault was Windows OpenSSH agent forwarding (`ssh -A`): it lists the key but can't sign with it ("chan_shutdown_read ... Not a socket"). |
| 3 | [03-first-playbook-recap.png](03-first-playbook-recap.png) | `site.yml` PLAY RECAP, all hosts `unreachable=0 failed=0` | After giving ops-ctl its own admin key (`lab/azure/bootstrap-admin-key.sh`), the lab built cleanly: nginx + 10-day cert, demo service, `/data` disk, `opswatch` user. |
| 4 | [04-opswatch-user-key-only-login.png](04-opswatch-user-key-only-login.png) | `web01 as opswatch`, `app01 as opswatch`, `stor01 as opswatch` with `-i ~/.ssh/opswatch -o IdentitiesOnly=yes -o BatchMode=yes` | The monitoring account works with its own key only, the way OpsWatch connects. |
| 5 | [05-first-live-dry-run-web01-403.png](05-first-live-dry-run-web01-403.png) | First `python -m opswatch run` on live hosts: 14 of 15 as predicted, `web01 http Critical HTTP 403` | OpsWatch caught a real lab misconfiguration: the nginx site had no `index` directive and Ubuntu ships `index.nginx-debian.html`, so `/` returned 403. Fixed in commit affb3e3. |
| 6 | [06-playbook-rerun-idempotent.png](06-playbook-rerun-idempotent.png) | Re-run: web01 `changed=2`, all others `changed=0`, stor01 `skipped=1` | Idempotency: only the nginx fix was applied; the data disk was not reformatted. |
| 7 | [07-dry-run-after-nginx-fix.png](07-dry-run-after-nginx-fix.png) | Dry run after the fix: `web01 http OK 200 in 0.02s`; cert Warning 9 days; app01/stor01 http and cert Not applicable; everything else OK | Fix verified by the same check that caught it. |
| 8 | [08-first-stored-run.png](08-first-stored-run.png) | `run --store`: slot 5980816 committed, 15 results, 0 incidents, `not_ok` = web01/cert Warning + 4 Not applicable; 9.841 s process start to commit | First real-lab timing. Under 15 s, but far slower than expected for healthy hosts: under investigation (forwarded SSH agent tried before the opswatch key). |
| 9 | [09-dry-run-without-agent-1.6s.png](09-dry-run-without-agent-1.6s.png) | `time (SSH_AUTH_SOCK= python -m opswatch run)`: real 1.568 s | Confirms the cause: with the forwarded agent hidden, a full healthy run takes 1.6 s instead of 9.8 s. First fix attempt: `IdentitiesOnly=yes`. |
| 10 | [10-stored-run-identitiesonly-not-enough.png](10-stored-run-identitiesonly-not-enough.png) | Stored run with `IdentitiesOnly=yes`, agent still forwarded: 5.745 s, the 9 `Not a socket` lines still there | `IdentitiesOnly` limits which keys are offered, but ssh still asks the agent whether it holds the `-i` key, so it still touches the broken agent. Second fix: `IdentityAgent=none`, which stops ssh contacting any agent. |

## Problems hit and fixes

- **Azure capacity:** B1s was unavailable and B-series quota was 0, so the lab runs on `Standard_F1als_v7` in eastus, which is NVMe-only. `main.bicep` got a `diskControllerType` parameter, and the playbook finds the data disk under either SCSI or NVMe naming.
- **Agent forwarding from Windows:** replaced by a dedicated ops-ctl admin key, scripted in `lab/azure/bootstrap-admin-key.sh`.
- **Slow SSH checks (9.8 s):** every check's ssh tried the broken forwarded agent before the opswatch key, bringing healthy runs close to the 10 s per-check deadline. Fixed with `IdentitiesOnly=yes` plus `IdentityAgent=none` in OpsWatch's SSH options (the first alone was not enough, see #10).
- **nginx 403 on web01:** found by OpsWatch itself; fixed with `index index.html index.nginx-debian.html;`.
