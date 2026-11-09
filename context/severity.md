# Severity standard and incident rules

## Severity levels

| Level    | Meaning                                                     | Incident?                                  |
|----------|-------------------------------------------------------------|--------------------------------------------|
| OK       | Check ran and passed                                        | Counts toward resolving an open incident   |
| Warning  | Check ran; needs attention soon                             | Counts toward opening an incident          |
| Critical | Check ran; service impact now or imminent                   | Counts toward opening an incident          |
| Unknown  | Check could not run or could not decide: SSH timeout, auth failure, bad output | Counts toward opening an incident when applicable; never displayed as OK |

**Rollup and comparison order:** Critical > Unknown > Warning > OK. Used to roll up several observations inside one check (SPEC §3.3) and to pick an incident's opening severity.

**Inapplicable results** (`applicable = false`, stored as Unknown with value `Not applicable`, SPEC §3.2) are not counted as OK or non-OK. Incident logic identifies them by the `applicable` field, never by parsing `value`.

## Incident rules

An incident is keyed by **(host, check)**. At most one open incident exists per key. Which URL, mount or unit caused the failure doesn't matter: different targets failing on consecutive runs count toward the same incident.

Streaks are evaluated in **slot order** (SPEC §6.3), once per committed run.

1. **Non-OK** means Warning, Critical, or applicable Unknown.
2. **Open:** two applicable non-OK results in **consecutive slots** open an incident.
   - Opening severity = the higher of the two under the rollup order.
   - `opened_at` = the second result's timestamp.
   - Both results are linked to the incident, and their evidence is kept as the opening evidence until resolution.
3. **Update:** while an incident is open, each non-OK result is linked to it. If its severity differs from the incident's current severity, the current severity changes and the change is recorded with its slot and timestamp. No second incident opens.
4. **Resolve:** two OK results in **consecutive slots** resolve the open incident. `resolved_at` = the second OK's timestamp. Both OK results are linked to the incident.
5. **Streak resets:**
   - An OK resets the opening streak.
   - A non-OK resets the recovery streak.
   - An inapplicable result resets both streaks and never opens or resolves anything.
   - A missed slot (the previous evaluated slot for this key isn't `slot − 1`) resets both streaks before the current result is counted.
6. **Open incidents persist:** missed slots and inapplicable results never close an open incident. Only the recovery rule does.
7. **History:** every result stays in result history, including the ones between opening and resolution.
8. **Exactly once:** each slot is committed once (SPEC §6.2). Retrying a slot that's already committed changes nothing.
9. **Resolution notes:** a person may add free-text notes to any incident. Notes never change status.

## Worked examples

(stor01, disk), consecutive slots: OK, Warning, Warning, Critical, OK, OK

- Slot 3: incident opens at Warning (higher of Warning, Warning).
- Slot 4: same incident, current severity becomes Critical.
- Slot 6: incident resolves. Exactly one incident in total.

(web01, http), slots 10, 11: Unknown, Critical → opens at Critical.

(app01, services), slots 20, 22 (slot 21 missed): Critical, Critical → nothing opens; the gap broke the streak. Slot 23 Critical → opens (slots 22, 23).

## Why 2-in-a-row

At a 5-minute interval this adds up to 10 minutes of delay to open and to resolve. For a lab that's acceptable, and it stops a check that flaps between Warning and OK from filling the incidents table with noise.
