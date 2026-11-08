# Old nginx tuning notes (2023)

These notes are from an unrelated project. They are not part of OpsWatch.

- Raised `worker_connections` to 4096 after load testing showed queueing at ~1,800 concurrent clients.
- Switched `keepalive_timeout` from 75s to 15s to free workers faster behind the load balancer.
- Enabled `gzip` for `text/css` and `application/javascript`; skipped images.
- Tried `proxy_cache` for the API; reverted because cache invalidation on deploy was unreliable.
- `client_max_body_size` set to 25m for the upload endpoint only.
- Log format extended with `$upstream_response_time` to debug slow upstreams.
- Considered moving TLS termination to the load balancer; never finished.
