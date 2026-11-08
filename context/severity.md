# Severity standard and incident rules

## Severity levels

| Level    | Meaning                                                     | Incident?                                  |
|----------|-------------------------------------------------------------|--------------------------------------------|
| OK       | Check ran and passed                                        | Counts toward resolving an open incident   |
| Warning  | Check ran; needs attention soon                             | Counts toward opening an incident          |
| Critical | Check ran; service impact now or imminent                   | Counts toward opening an incident          |
| Unknown  | Check could not run: SSH timeout, auth failure, bad output  | Counts toward opening an incident; never displayed as OK |

Order of badness: OK < Warning < Critical. Unknown is its own state, shown distinctly, and is never folded into OK.

## Incident rules

An incident is keyed by **(host, check)**. At most one open incident exists per key.

1. **Open:** an incident opens when a key has **2 consecutive non-OK results** (any mix of Warning, Critical, Unknown). The incident's `opened_at` is the timestamp of the second result. Its evidence is both results' evidence.
2. **Update:** while an incident is open, every non-OK result is attached to it. If the severity changes (for example Warning → Critical, or Critical → Unknown), the incident's current severity is updated and the change is recorded with its timestamp. No new incident is opened.
3. **Resolve:** an open incident resolves when its key has **2 consecutive OK results**. `resolved_at` is the timestamp of the second OK.
4. **Single blips:** one non-OK result between OKs opens nothing. One OK result inside an open incident resolves nothing.
5. **Resolution notes:** a person may add free-text notes to any incident. Notes never change status.

## Worked example

Results for (stor01, disk) in order: OK, Warning, Warning, Critical, OK, OK

- Result 3: incident opens at Warning.
- Result 4: same incident, severity updated to Critical.
- Result 6: incident resolves.
- Exactly one incident in total.

## Why 2-in-a-row

At a 5-minute interval this adds up to 10 minutes of delay to open and to resolve. For a lab that's acceptable, and it stops a check that flaps between Warning and OK from filling the incidents table with noise.
