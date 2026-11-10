#!/usr/bin/env bash
# Proves the scheduler end to end on ops-ctl (SPEC §6.3): the timer is armed, systemd starts
# opswatch.service at the next 5-minute slot, the service exits 0, and that slot is committed
# in SQLite exactly once with one result per host per check.
# Read-only: it waits and inspects; it never starts or changes anything.
# Usage (on ops-ctl, after installing the units):  bash deploy/verify-timer.sh
set -uo pipefail

DB=${OPSWATCH_DB:-/var/lib/opswatch/opswatch.db}
EXPECTED_RESULTS=${EXPECTED_RESULTS:-15}   # 3 hosts x 5 checks in the lab
fails=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; fails=$((fails + 1)); }

# 1. Timer armed
if systemctl is-enabled --quiet opswatch.timer && systemctl is-active --quiet opswatch.timer; then
  pass "opswatch.timer is enabled and active"
else
  fail "opswatch.timer is not enabled and active"; exit 1
fi

next=$(systemctl show opswatch.timer -p NextElapseUSecRealtime --value)
fire=$(date -d "$next" +%s 2>/dev/null || true)
if [ -z "$fire" ]; then fail "could not read the timer's next firing ('$next')"; exit 1; fi
slot=$((fire / 300))
if [ $((fire % 300)) -eq 0 ]; then
  pass "next firing $(date -u -d "@$fire" +%H:%M:%SZ) is on a 5-minute boundary (slot $slot)"
else
  fail "next firing $(date -u -d "@$fire" +%H:%M:%SZ) is not on a 5-minute boundary"
fi

# 2. Wait for that firing and for the run to finish (at most 90 s after it)
echo "....  waiting $(( fire - $(date +%s) )) s for the timer, then up to 90 s for the run"
deadline=$((fire + 90))
committed=""
while [ "$(date +%s)" -lt "$deadline" ]; do
  committed=$(sqlite3 "$DB" "SELECT COUNT(*) FROM runs WHERE slot = $slot;" 2>/dev/null || true)
  [ "$committed" = "1" ] && [ "$(systemctl show opswatch.service -p ActiveState --value)" != "activating" ] && break
  sleep 5
done

# 3. systemd launched it at that slot and it exited cleanly
started=$(systemctl show opswatch.service -p ExecMainStartTimestamp --value)
started_s=$(date -d "$started" +%s 2>/dev/null || echo 0)
status=$(systemctl show opswatch.service -p ExecMainStatus --value)
if [ "$started_s" -ge "$fire" ] && [ "$started_s" -le $((fire + 5)) ]; then
  pass "systemd started opswatch.service at $(date -u -d "@$started_s" +%H:%M:%SZ), within 5 s of the slot"
else
  fail "opswatch.service last started at '$started', not at the slot start"
fi
if [ "$status" = "0" ]; then pass "opswatch.service exited 0"; else fail "opswatch.service exit status $status"; fi
if journalctl -u opswatch.service --since "@$fire" --no-pager -q 2>/dev/null | grep -q '"committed": true'; then
  pass "journal shows the run's summary with \"committed\": true"
else
  fail "journal has no committed summary since the slot started (is azureuser in the adm group?)"
fi

# 4. Exactly one committed run for the slot, with one result per host per check
results=$(sqlite3 "$DB" "SELECT COUNT(*) FROM check_results WHERE slot = $slot;" 2>/dev/null || echo 0)
dupes=$(sqlite3 "$DB" "SELECT COUNT(*) FROM (SELECT host, check_name FROM check_results WHERE slot = $slot GROUP BY 1, 2 HAVING COUNT(*) > 1);" 2>/dev/null || echo 0)
[ "$committed" = "1" ] && pass "slot $slot committed exactly once" || fail "slot $slot committed ${committed:-0} times"
[ "$results" = "$EXPECTED_RESULTS" ] && pass "slot $slot has $results results" || fail "slot $slot has $results results, expected $EXPECTED_RESULTS"
[ "$dupes" = "0" ] && pass "no host/check appears twice in slot $slot" || fail "$dupes host/check pairs appear twice"

echo
if [ "$fails" -eq 0 ]; then echo "SCHEDULER VERIFIED"; exit 0; else echo "SCHEDULER NOT VERIFIED ($fails failed)"; exit 1; fi
