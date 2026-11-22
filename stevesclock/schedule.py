"""Quarter-hour scheduling and the headless daemon loop.

Mirrors the Mac build: all four quarter-hour moments are watched, and whether a
slot makes a sound is decided by its voice setting, not by an enable flag. The
alarm fires at its configured time, falls back to the main voice, and ignores
quiet mode.

Boundaries are recomputed from the wall clock every time round, so a clock
change or a suspend/resume does not leave the daemon drifting. If it wakes more
than two minutes late the announcement is skipped rather than spoken stale.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from . import tts

#: Announcements later than this are considered missed.
LATE_TOLERANCE = 120

#: Longest single sleep. Short enough to notice a system clock change.
TICK = 5.0

#: Which slot each quarter-hour moment belongs to.
SLOT_BY_MINUTE = {0: "hour", 15: "quarter", 30: "half", 45: "three_quarter"}


def boundary_after(now, minute):
    """The next time the wall clock shows ``minute`` past the hour."""
    candidate = now.replace(minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(hours=1)
    return candidate


def next_alarm(now, alarm_minutes):
    """The next occurrence of ``alarm_minutes`` past midnight."""
    candidate = now.replace(
        hour=alarm_minutes // 60,
        minute=alarm_minutes % 60,
        second=0,
        microsecond=0,
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def next_event(now, minutes, alarm_minutes=None):
    """(when, slot) for the next thing to do."""
    events = [(boundary_after(now, minute), SLOT_BY_MINUTE.get(minute, "hour")) for minute in minutes]
    if alarm_minutes is not None:
        events.append((next_alarm(now, alarm_minutes), "alarm"))
    if not events:
        return None, None
    return min(events)


def in_quiet(now, window):
    """True if ``now`` falls inside the quiet window.

    ``window`` is (stop_minutes, start_minutes): quiet from the moment speech
    should stop until the moment it should start again.
    """
    if not window:
        return False
    stop, start = window
    if stop == start:
        return False
    current = now.hour * 60 + now.minute
    if stop > start:
        return current >= stop or current < start
    return current >= stop and current < start


def run(config, announce, log=print):
    """Loop forever, announcing on the configured boundaries."""
    minutes = sorted(SLOT_BY_MINUTE)
    alarm_minutes = config.alarm_time()

    log(
        "watching minutes %s%s"
        % (
            ", ".join("%02d" % minute for minute in minutes),
            ", alarm at %02d:%02d" % (alarm_minutes // 60, alarm_minutes % 60)
            if alarm_minutes is not None
            else "",
        )
    )
    for slot in ("main", "quarter", "half", "three_quarter", "hour", "alarm"):
        log("  %-14s %s" % (slot, config.slot_voice(slot)))

    fired = set()
    while True:
        now = datetime.now()
        target, slot = next_event(now, minutes, alarm_minutes)
        if target is None:
            log("nothing scheduled - stopping")
            return 0

        while True:
            remaining = (target - datetime.now()).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(remaining, TICK))

        late = (datetime.now() - target).total_seconds()
        key = (target.strftime("%Y%m%d%H%M"), slot)
        if key in fired:
            time.sleep(1)
            continue

        if late > LATE_TOLERANCE:
            log("%s skipped (woke %.0fs late)" % (target.strftime("%H:%M"), late))
            time.sleep(1)
            continue

        fired.add(key)
        silent_slot = (
            slot != "alarm"
            and config.slot_voice(slot) == tts.NONE
        )
        if silent_slot:
            pass  # slot set to None: nothing to say, as on the Mac
        elif slot == "alarm":
            try:
                log("%s alarm" % target.strftime("%H:%M"))
                announce(target, "alarm")
            except Exception as error:  # a failed alarm must not kill the daemon
                log("alarm failed: %s" % error)
        elif in_quiet(target, config.quiet_window()):
            log("%s quiet" % target.strftime("%H:%M"))
        else:
            try:
                log("%s %s" % (target.strftime("%H:%M"), announce(target, slot)))
            except Exception as error:
                log("announcement failed: %s" % error)

        # Forget keys older than yesterday so the set cannot grow forever.
        if len(fired) > 64:
            cutoff = (now - timedelta(days=1)).strftime("%Y%m%d")
            fired = {item for item in fired if item[0][:8] >= cutoff}

        time.sleep(1)
