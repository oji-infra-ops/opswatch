# OpsWatch v1 — Specification

OpsWatch is an agentless Linux health console. One control node (`ops-ctl`) connects over SSH to three lab hosts every 5 minutes, runs five checks, stores every result in SQLite, and turns failures into incidents with stored evidence. It observes and records. It never changes a host.

## 1. Lab inventory

| Host    | IP              | Role         | What's on it                                         | Used to test            |
|---------|-----------------|--------------|------------------------------------------------------|-------------------------|
| ops-ctl | 192.168.56.10   | Control node | OpsWatch code, SQLite DB, SSH private key, systemd timer | Runs everything      |
| web01   | 192.168.56.11   | Web server   | nginx on 443, self-signed cert valid 10 days         | HTTP, cert expiry       |
| app01   | 192.168.56.12   | App server   | `opswatch-demo.service` (dummy systemd unit)         | Services                |
| stor01  | 192.168.56.13   | Storage      | 2 GB data disk mounted at `/data`                    | Disk thresholds         |

The authoritative host list is `inventory.yaml`. Code must never hard-code hosts, IPs, users, key paths or URLs.

## 2. Access

- Each monitored host has a dedicated `opswatch` user, key-based SSH only.
- Every remote command is read-only. No `sudo`.
- SSH options for every connection: `BatchMode=yes`, `ConnectTimeout=10`, `StrictHostKeyChecking=accept-new`.
- Every remote command has a hard total timeout of 10 seconds (connect + run). A timeout is Unknown, never OK.

## 3. Result shape

Every check, for every host, returns exactly one result:

| Field       | Type   | Meaning                                                                 |
|-------------|--------|-------------------------------------------------------------------------|
| `host`      | string | Inventory name, e.g. `stor01`                                           |
| `check`     | string | One of `disk`, `services`, `uptime`, `http`, `cert`                     |
| `severity`  | string | One of `OK`, `Warning`, `Critical`, `Unknown`                           |
| `value`     | string | Short human-readable measurement, e.g. `/data 92%`, `opswatch-demo inactive` |
| `evidence`  | string | Raw command output (or error text) the severity was decided from. Never empty. |
| `timestamp` | string | UTC, ISO 8601, e.g. `2026-11-09T14:05:00Z`                              |

## 4. The five checks

### 4.1 Disk usage (`disk`) — over SSH
- Command: `df -P`
- Ignore filesystems of type `tmpfs` and `devtmpfs` (match on the first column).
- Evaluate every remaining mount. Severity is the worst across mounts; `value` names the worst mount and its percentage.
- Warning: any mount ≥ 80%. Critical: any mount ≥ 90%.
- Unknown when: SSH fails or times out; output has no header line; a data line can't be parsed into 6 columns; the use% column isn't `N%`.

### 4.2 Services (`services`) — over SSH
Two parts, both run in the same SSH session:
1. `systemctl --failed --no-legend --plain` — any line of output is a failed unit.
2. For each unit listed under `services` for that host in `inventory.yaml`: `systemctl is-active <unit>`.

Why both: `systemctl stop` leaves a unit **inactive**, not **failed**, so part 1 alone misses a deliberately stopped service.

- OK: no failed units and every listed unit prints `active`.
- Critical: any failed unit, or any listed unit prints anything other than `active` (`inactive`, `failed`, `activating`, `unknown`).
- No Warning level.
- Unknown when: SSH fails or times out, or `is-active` output is empty.
- Note: `systemctl is-active` exits non-zero when the unit isn't active. A non-zero exit here is a result, not an error.

### 4.3 Uptime (`uptime`) — over SSH
- Command: `cat /proc/uptime` — first field is seconds since boot.
- Warning: < 3600 seconds (rebooted in the last hour).
- No Critical level.
- Unknown when: SSH fails or times out, or the first field isn't a number.

### 4.4 HTTP availability (`http`) — from ops-ctl, not over SSH
- GET each URL listed under `urls` for that host, 5-second timeout, no redirects followed.
- TLS: trust the host's certificate explicitly (path in `inventory.yaml`). Do not disable verification globally.
- OK: status 2xx or 3xx and response time ≤ 2 s.
- Warning: status 2xx or 3xx and response time > 2 s.
- Critical: status 4xx/5xx, connection refused, or timeout.
- Unknown when: the URL is malformed or DNS/route errors prevent any attempt.
- Evidence: status code, response time, and the error text if any.

### 4.5 Certificate expiry (`cert`) — from ops-ctl, not over SSH
- TLS handshake to each `https` URL for that host; read the certificate's `notAfter`.
- Warning: < 14 days left. Critical: < 3 days left, or already expired.
- Unknown when: the handshake fails for any reason other than expiry.
- `value` is days left as an integer, e.g. `9 days`.

## 5. Severity and incidents

See `severity.md`. Summary: Unknown is never shown as OK; incidents open after 2 consecutive non-OK results and resolve after 2 consecutive OK results.

## 6. Isolation

- One host failing must never stop or delay the checks on another host. Hosts are checked in parallel.
- One check failing must never stop the other checks on the same host.
- A full run against 3 hosts, with one host powered off, finishes in under 15 seconds.
- A parser exception becomes an Unknown result with the exception text as evidence. The run never crashes.

## 7. Out of scope for v1

Prometheus, email or Slack alerts, Docker, user login, more than three hosts, any action that changes a host.
