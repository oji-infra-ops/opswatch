# OpsWatch v1 — Specification

OpsWatch is an agentless Linux health console. One control node (`ops-ctl`) connects over SSH to the hosts in `inventory.yaml` on fixed five-minute slots, runs five checks per host, stores every result in SQLite, and turns failures into incidents with stored evidence. It observes and records. It never changes a host.

## 1. Inventory

`inventory.yaml` is the **only** runtime source of hosts, addresses, SSH users, key paths, URLs and trust anchors. Code never hard-codes any of them. The table below is the lab fixture the inventory describes, not a runtime input. The lab runs on Azure (`lab/azure/main.bicep`, built by `lab/ansible/site.yml`).

| Host    | Address        | Role         | What's on it                                             | Used to test        |
|---------|----------------|--------------|----------------------------------------------------------|---------------------|
| ops-ctl | 10.10.1.10     | Control node | OpsWatch code, SQLite DB, SSH private key, systemd timer | Runs everything     |
| web01   | 10.10.1.11     | Web server   | nginx on 443, self-signed cert for `web01` valid 10 days | HTTP, cert expiry   |
| app01   | 10.10.1.12     | App server   | `opswatch-demo.service` (dummy systemd unit)             | Services            |
| stor01  | 10.10.1.13     | Storage      | 4 GiB Azure data disk mounted at `/data`                 | Disk thresholds     |

Inventory validation happens before any probe starts. A run is refused (run-level error, nothing written) when:

- the file is missing or isn't valid YAML;
- `hosts` is missing or empty;
- a host name appears twice;
- a host has no `address`, or no SSH user or key path (after applying `defaults`);
- `services` or `urls` is present but isn't a list of strings.

An omitted `services` or `urls` list is treated as empty. A malformed individual URL does **not** refuse the run: it becomes an Unknown observation inside that host's HTTP and certificate checks, and every other URL and check still runs.

Lab note: the web01 URL is `https://web01/`, with `web01` resolved through `/etc/hosts` on ops-ctl and the certificate carrying `subjectAltName=DNS:web01`. Hostname verification stays on (§4.4), so a URL by IP address would fail validation.

## 2. Access and deadlines

- Each monitored host has a dedicated `opswatch` user, key-based SSH only. Every remote command is read-only. No `sudo`.
- SSH options for every connection: `BatchMode=yes`, `ConnectTimeout=10`, `StrictHostKeyChecking=accept-new`.
- Every remote command runs with `LC_ALL=C` so output format is predictable.
- **One hard 10-second deadline per SSH check**, covering connection and every command in it. The services check runs both of its parts in one SSH session under that one deadline. When the deadline passes, the `ssh` process and its children are killed and the check is Unknown.
- HTTP and certificate probes have a 5-second end-to-end deadline per URL (§4.4, §4.5).

## 3. Results

### 3.1 Result shape

Every completed run persists **exactly one result per inventory host per check**. The three-host lab therefore produces 15 result rows per run.

| Field        | Type    | Meaning                                                                                       |
|--------------|---------|-----------------------------------------------------------------------------------------------|
| `host`       | string  | Inventory name, e.g. `stor01`                                                                  |
| `check`      | string  | One of `disk`, `services`, `uptime`, `http`, `cert`                                            |
| `severity`   | string  | One of `OK`, `Warning`, `Critical`, `Unknown`                                                  |
| `applicable` | boolean | `false` only when the check has nothing to examine on this host (§3.2). Incident logic reads this field, never `value`. |
| `value`      | string  | Short human-readable measurement, e.g. `/data 92%`, `opswatch-demo inactive`, `Not applicable` |
| `evidence`   | string  | Raw output, observations or error text the severity was decided from. Never empty.             |
| `timestamp`  | string  | UTC time the check **completed**, ISO 8601 with `Z`, e.g. `2026-11-09T14:05:03Z`               |
| `slot`       | integer | The run's schedule slot (§6.3). Incident evaluation orders by slot, never by timestamp.        |

### 3.2 Inapplicable checks

- HTTP is inapplicable when the host's `urls` list is missing or empty.
- Certificate is inapplicable when the host has no `https://` URL and no malformed URL. A host with only `http://` URLs has an applicable HTTP check and an inapplicable certificate check.
- An inapplicable result is stored with `severity = Unknown`, `applicable = false`, `value = Not applicable`, and evidence saying why (for example `no urls configured for host`).
- Inapplicable results never open or resolve incidents, and they break any pending streak (`severity.md`).

### 3.3 Rolling up several observations

Some checks examine several things (URLs, mounts, units). The rollup order is:

**Critical > Unknown > Warning > OK**

The check's severity is the worst observation under that order. Ties go to the first item in inventory order (URLs, units) or output order (mounts). `value` names the selected item; `evidence` keeps the observations for every item examined.

## 4. The five checks

### 4.1 Disk usage (`disk`) — over SSH

- Command: `LC_ALL=C df -P`.
- The header must be exactly `Filesystem 1024-blocks Used Available Capacity Mounted on` (whitespace-separated). Anything else is Unknown.
- Each data row is parsed as five whitespace-separated fields plus the remainder of the line as the mount path, so mount paths containing spaces are accepted.
- Rows whose **first field is exactly** `tmpfs` or `devtmpfs` are excluded. This is source-name matching on the filesystem column, not a filesystem-type lookup.
- The capacity field must be an integer followed by `%`.
- Unknown when: SSH fails or times out; `df` exits non-zero; the header is wrong; **any** remaining row is malformed (even if another row is Critical); or no rows remain after exclusion.
- Otherwise the highest percentage wins; ties go to the first row in output order.
- Warning: ≥ 80%. Critical: ≥ 90%. Both thresholds are inclusive.
- `value`: `<mount> <pct>%`, e.g. `/data 92%`.

### 4.2 Services (`services`) — over SSH, one session

Two parts run in a single SSH session under the shared 10-second deadline:

1. `systemctl --failed --no-legend --plain` — a successful query lists failed units of every type. Each non-empty stdout line is a failed unit.
2. For each unit in the host's `services` list, in inventory order: `systemctl is-active -- <unit>`.

Why both: `systemctl stop` leaves a unit *inactive*, not *failed*, so part 1 alone misses a deliberately stopped service.

Each part produces observations:

| Observation                                  | Severity |
|----------------------------------------------|----------|
| `--failed` query succeeded, no units listed  | OK       |
| `--failed` query succeeded, ≥ 1 unit listed  | Critical |
| `--failed` query exited non-zero             | Unknown  |
| `is-active` printed `active` (after trimming whitespace) | OK |
| `is-active` printed any other non-empty state | Critical, regardless of exit code |
| `is-active` printed nothing                  | Unknown  |

- The check rolls up with §3.3, so a confirmed Critical observation wins over another unit's missing output.
- An SSH transport failure (exit 255), a deadline expiry, or output missing its section markers makes the **whole** check Unknown.
- stderr is kept in the evidence for diagnosis; unit rows are parsed from stdout only.
- No Warning level.

### 4.3 Uptime (`uptime`) — over SSH

- Command: `cat /proc/uptime`. The first field is seconds since boot.
- It must parse as a finite, non-negative decimal number; otherwise Unknown.
- Warning: < 3600 seconds. OK: ≥ 3600 seconds. No Critical level.
- Unknown on SSH failure or timeout.

### 4.4 HTTP availability (`http`) — from ops-ctl, not over SSH

For each URL in the host's `urls` list:

- **Deadline:** 5 seconds end to end, covering DNS, connection, TLS and response headers.
- **Timing:** measured from immediately before the GET starts until the response headers arrive. The response is closed without reading the body.
- **Redirects** are not followed; a 3xx is judged as itself.
- **Trust:** when the host has `ca_cert`, that file is the trust anchor for that host's HTTPS requests only. Chain and hostname verification always stay on.

| Outcome                                                        | Severity |
|----------------------------------------------------------------|----------|
| 2xx or 3xx, elapsed ≤ 2.0 s                                    | OK       |
| 2xx or 3xx, elapsed > 2.0 s                                    | Warning  |
| 4xx or 5xx                                                     | Critical |
| Connection refused                                             | Critical |
| Deadline expired                                               | Critical |
| TLS certificate validation failure (chain, hostname, expiry)   | Critical |
| Malformed URL                                                  | Unknown  |
| Explicit DNS failure, or no route to host / network unreachable | Unknown |
| Anything else the check can't classify                         | Unknown  |

Evidence for each URL records elapsed time, the status code when there is one, and the error text when there is one. URLs roll up with §3.3. URL probes run with bounded concurrency so one stalled URL can't hold up other checks.

### 4.5 Certificate expiry (`cert`) — from ops-ctl, not over SSH

For each `https://` URL (plus any malformed URL, which is an Unknown observation):

- TLS handshake to the URL's hostname and port (default 443), SNI and hostname verification on, 5-second end-to-end deadline, same trust rules as §4.4. The leaf certificate is inspected.
- If the handshake validates, classify the leaf's `notAfter`.
- If validation fails **because the certificate has expired**, OpsWatch does a second handshake with only the time check disabled. TLS libraries stop at the first verification error, so an expiry error alone doesn't prove the chain and hostname are fine. If the second handshake validates, expiry was the sole failure: Critical, `value = expired`. If it fails, Unknown.
- Any other validation failure, or a failure the check can't explain: Unknown.

Thresholds use the exact remaining time `notAfter − now`:

| Remaining time                          | Severity |
|-----------------------------------------|----------|
| ≤ 0 (at or after `notAfter`)            | Critical, `value = expired` |
| > 0 and < 3 days                        | Critical |
| ≥ 3 days and < 14 days                  | Warning  |
| ≥ 14 days                               | OK       |

For unexpired certificates, `value` shows remaining whole days rounded down (display only), e.g. `9 days`. Evidence always includes `notAfter`. URLs roll up with §3.3.

## 5. Severity and incidents

See `severity.md`. Summary: Unknown is never shown as OK; incidents are keyed by `(host, check)`, open after 2 consecutive applicable non-OK results in consecutive slots, and resolve after 2 consecutive OK results.

## 6. Isolation, persistence and scheduling

### 6.1 Isolation

- All hosts and all their checks run concurrently. One host or check failing never stops or delays another.
- A parser exception becomes an Unknown result with the exception text as evidence. The run never crashes because of one check.
- Acceptance: a full run of the three-host lab with one host powered off finishes in **under 15 seconds, measured from process start through the SQLite commit**, and the healthy hosts' checks complete independently of the offline host's deadline.

### 6.2 Persistence

- A run's results, incident changes and run record are written in **one SQLite transaction**. On any write failure the transaction rolls back, nothing from that run is kept, and the run reports a run-level error with a non-zero exit.
- A run counts as complete only after its commit.
- The run's identity is its schedule slot. A slot can be committed once; a retry of an already-committed slot writes nothing and can't advance a streak twice.

### 6.3 Scheduling

- Runs happen on fixed five-minute slots aligned to the clock (`:00`, `:05`, `:10` …). Slot number = `floor(unix_time / 300)` at run start.
- Only one run at a time. If a run is still active when the next slot fires, that slot is skipped.
- Incident streaks use slot numbers to detect gaps, so a missed slot (including across a restart) breaks streaks.
- systemd: `OnCalendar=*:0/5`, `Type=oneshot`; OpsWatch also takes a non-blocking file lock so a manual `--store` run can't overlap a scheduled one.

## 7. Out of scope for v1

Prometheus, email or Slack alerts, Docker, user login, more than three hosts, any action that changes a host.
