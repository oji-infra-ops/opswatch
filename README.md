# OpsWatch

OpsWatch is an agentless Linux health console. A control node connects over SSH to a small set of Linux hosts every 5 minutes, checks disk usage, failed or stopped services, uptime, HTTP availability and TLS certificate expiry, and records every result in SQLite. When a check fails twice in a row it opens an incident with the raw evidence attached, and when the check passes twice in a row it resolves the incident on its own. A host that can't be reached shows as **Unknown**, never as healthy. OpsWatch only observes: it never changes a monitored host.

Status: in development. See `context/SPEC.md` for the v1 specification.
