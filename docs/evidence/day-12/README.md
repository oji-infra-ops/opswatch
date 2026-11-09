# Day 12 evidence: deploy as code

| File | What it proves |
|---|---|
| `01-deploy-as-code.png` | `ansible-playbook lab/ansible/site.yml` deploys OpsWatch itself: code to `/opt/opswatch`, data to `/var/lib/opswatch`, timer and web UI units installed and enabled. PLAY RECAP: ops-ctl `changed=8` (7 tasks plus the web UI restart handler), web01/app01/stor01 `changed=0`, every host `failed=0`. |
