# Design decisions

Each choice and the context file that drove it.

| Decision | Why | Source |
|---|---|---|
| Every host gets all five checks; HTTP/cert become `Unknown` + `applicable=false` + `Not applicable` when there's nothing to probe | One row per host per check (15 for the lab); incident logic reads `applicable`, never display text | SPEC §3.1–3.2 |
| Rollup order Critical > Unknown > Warning > OK, ties by inventory/output order | Used for URLs, units, mounts and opening severity | SPEC §3.3, severity.md |
| `ssh` binary via `subprocess`, new session per call, `killpg` on deadline | Hard 10 s deadline that also kills children; no paramiko | SPEC §2, CONVENTIONS |
| Services check is one shell script with `@@` section markers and exit codes | Both parts in one SSH session under one deadline; missing markers → whole check Unknown | SPEC §4.2 |
| `LC_ALL=C` on every remote command, exact `df -P` header match, `split(None, 5)` for rows | Predictable output; mount paths with spaces | SPEC §4.1 |
| HTTP/TLS probes run in a daemon thread joined with the deadline | Socket timeouts can't bound DNS lookups; the outer join can | SPEC §4.4 |
| Expired cert → second handshake with OpenSSL `X509_V_FLAG_NO_CHECK_TIME` (0x200000) | TLS stops at the first error; this proves expiry was the sole failure | SPEC §4.5 |
| `ca_cert` per host via `ssl.create_default_context(cafile=...)`, hostname check always on | Trust scoped to that host; no verification switched off | SPEC §4.4 |
| Run identity = schedule slot (`floor(unix_time / 300)`), `runs.slot` primary key | A retried slot writes nothing, so streaks can't advance twice | SPEC §6.2–6.3 |
| Streak state in a `streaks` table with `last_slot` | Gap detection survives restarts | severity.md rule 5 |
| One `BEGIN IMMEDIATE … COMMIT` per run; any error rolls back | No partial results or incident updates | SPEC §6.2 |
| Non-blocking `flock` on `<db>.lock`; held lock → slot skipped, exit 0 | One run at a time even for manual `--store` runs | SPEC §6.3 |
| Duplicate YAML keys rejected by a custom SafeLoader | PyYAML otherwise keeps the last duplicate host silently | SPEC §1 |
| `NOT-RELEVANT.md` not used | Old nginx notes; out of scope | context/NOT-RELEVANT.md |
