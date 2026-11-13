#!/usr/bin/env python3
"""Decide whether the scheduled workflow should run now, and whether it may publish.

GitHub cron has no time zone, so the workflow has two entries per run (daylight and standard time).
Only the entry that lands inside the Central-time window below proceeds; the other one stops here.
The windows are wide enough to absorb GitHub's usual start delay and too narrow for the wrong entry.

  gate.py EVENT LIVE_VAR LIVE_INPUT [--now 2026-11-13T12:54:00+00:00]
Prints run=true|false and live=true|false for $GITHUB_OUTPUT.
"""
import datetime as dt
import sys
from zoneinfo import ZoneInfo

WINDOWS = {"weekday": ((6, 25), (7, 45)), "saturday": ((8, 20), (9, 40))}


def decide(event, live_var, live_input, now):
    ct = now.astimezone(ZoneInfo("America/Chicago"))
    if event != "schedule":  # a manual run always goes ahead; it publishes only if asked to
        return True, live_input == "true"
    if ct.weekday() == 6:  # Sunday
        return False, False
    lo, hi = WINDOWS["saturday" if ct.weekday() == 5 else "weekday"]
    minutes = ct.hour * 60 + ct.minute
    inside = lo[0] * 60 + lo[1] <= minutes < hi[0] * 60 + hi[1]
    return inside, inside and live_var == "true"


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    event, live_var, live_input = (args + ["", "", ""])[:3]
    now = dt.datetime.now(dt.timezone.utc)
    if "--now" in sys.argv:
        now = dt.datetime.fromisoformat(sys.argv[sys.argv.index("--now") + 1])
    run, live = decide(event, live_var, live_input, now)
    print(f"run={str(run).lower()}")
    print(f"live={str(live).lower()}")
