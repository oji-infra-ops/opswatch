# Coding conventions

- Python 3.11+. Type hints on all public functions.
- Standard library first. Allowed dependencies: `pyyaml`, `pytest`, `ruff`; from Day 9, `fastapi`, `uvicorn`, `jinja2`. Ask before adding anything else.
- Storage is SQLite via the standard `sqlite3` module. No ORM. The DB path comes from the `OPSWATCH_DB` environment variable, default `~/opswatch.db`.
- SSH via the system `ssh` binary using `subprocess.run` with an explicit `timeout`. No paramiko.
- Every check returns the same result shape (see `SPEC.md` section 3). One function per check; the parser is a separate pure function that takes command output and returns a result, so it can be tested against `samples/` without a lab.
- Package layout: `opswatch/` (code), `tests/` (pytest), entry point `python -m opswatch run`.
- Every parser has at least one test with a healthy sample, one with a failing sample, and one with garbage input that must produce Unknown.
- Tests never touch the network or the lab.
- `ruff check .` passes before every commit.
- Timestamps are always UTC, ISO 8601, with a `Z` suffix.
- No command sent to a monitored host may modify it.
- Write `DECISIONS.md` in the repo root: every design choice and which context file drove it.
