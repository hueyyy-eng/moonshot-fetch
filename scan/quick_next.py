"""When should the next quick check start? (owner, 13 Nov 2026: make the 2-hourly check start reliably)

    python3 scan/quick_next.py            # prints wait=<seconds>, relay=<true|false>, slot=<HH:MM> for $GITHUB_OUTPUT

GitHub drops many timed ("cron") starts when it is busy (12 Nov: 2 of 8 quick checks ran), but a "Run workflow"
press starts within a minute. So each quick check ends with a job that waits until the next slot and presses that
button itself (.github/workflows/quick.yml, job "next"). Slots: 7:23am, 9:23am ... 9:23pm SGT. The next slot is at
least 30 minutes away (a late check does not get a second one straight after it). A GitHub job may run 6 hours at
most, so the overnight gap (9:23pm -> 7:23am) is covered by one "relay" run that only waits and passes the baton on.
Standard library only, so it runs without installing anything.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

SGT = timezone(timedelta(hours=8))
SLOT_HOURS = (7, 9, 11, 13, 15, 17, 19, 21)
SLOT_MINUTE = 23
MIN_GAP = timedelta(minutes=30)
MAX_WAIT = timedelta(minutes=330)          # stay well inside the job's 6-hour limit


def plan(now: datetime) -> tuple[int, bool, datetime]:
    """-> (seconds to wait, relay = wait only and pass on (no check at the end), the slot aimed at)"""
    now = now.astimezone(SGT)
    slots = [datetime(now.year, now.month, now.day, h, SLOT_MINUTE, tzinfo=SGT) + timedelta(days=d)
             for d in (0, 1) for h in SLOT_HOURS]
    slot = min(s for s in slots if s >= now + MIN_GAP)
    wait = slot - now
    if wait > MAX_WAIT:
        return int(MAX_WAIT.total_seconds()), True, slot
    return int(wait.total_seconds()), False, slot


if __name__ == "__main__":
    w, relay, slot = plan(datetime.now(timezone.utc))
    print(f"wait={w}\nrelay={'true' if relay else 'false'}\nslot={slot:%H:%M}")
    sys.exit(0)
