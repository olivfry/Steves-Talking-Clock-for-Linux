"""Configuration and XDG paths.

The settings names follow the Mac build's @AppStorage keys so the two ports
describe the same thing in the same words: one voice per announcement slot, a
gap delay in milliseconds (0.03 s on the Mac), alarm time, and a quiet window
with "stop speaking after" / "start speaking from".
"""

from __future__ import annotations

import configparser
import os
import re
from pathlib import Path

APP = "steves-clock"

#: Announcement slots, in the Mac build's order.
SLOTS = ("main", "quarter", "half", "three_quarter", "hour", "alarm")

#: Human labels, matching the Mac picker labels.
SLOT_LABELS = {
    "main": "Main Voice List",
    "quarter": "Quarter Hour Voice List",
    "half": "Half Hour Voice List",
    "three_quarter": "Three Quarter Hour Voice List",
    "hour": "On the Hour Voice List",
    "alarm": "Alarm Voice List",
}

#: Week days in the order Python numbers them, Monday first.
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

DAY_LABELS = {
    "mon": "Mon",
    "tue": "Tue",
    "wed": "Wed",
    "thu": "Thu",
    "fri": "Fri",
    "sat": "Sat",
    "sun": "Sun",
}

DEFAULTS = {
    "general": {
        "voices_dir": "",
        "player": "",
        "device": "",
        "gap_ms": "30",
        "hour24": "false",
        "check_updates": "true",
    },
    "voices": {
        "main": "Default",
        "quarter": "None",
        "half": "None",
        "three_quarter": "None",
        "hour": "None",
        "alarm": "None",
    },
    "quiet": {
        "enabled": "false",
        "stop": "11:00 pm",
        "start": "08:00 am",
        "days": "every",
    },
    "alarm": {
        "time": "08:00 am",
    },
}

TRUE = {"1", "true", "yes", "on"}


def _xdg(env, fallback):
    return Path(os.environ.get(env) or Path.home() / fallback)


def config_dir():
    return _xdg("XDG_CONFIG_HOME", ".config") / APP


def config_path():
    return config_dir() / "config.conf"


def data_dir():
    return _xdg("XDG_DATA_HOME", ".local/share") / APP


def default_voices_dir():
    return data_dir() / "voices"


class Config:
    """Typed access to the INI file, with defaults for anything unset."""

    def __init__(self, path=None):
        self.path = Path(path) if path else config_path()
        self._parser = configparser.ConfigParser()
        self.loaded = False
        self.load()

    # -- loading ---------------------------------------------------------

    def load(self):
        raw = configparser.ConfigParser()
        if self.path.is_file():
            raw.read(self.path)
            self.loaded = True
        self._migrate(raw)

        parser = configparser.ConfigParser()
        for section, values in DEFAULTS.items():
            parser[section] = dict(values)
        for section in raw.sections():
            if not parser.has_section(section):
                parser.add_section(section)
            for key, value in raw.items(section):
                parser.set(section, key, value)
        self._parser = parser
        return self

    @staticmethod
    def _migrate(raw):
        """Bring a config from the 0.3 layout forward, in place."""
        if raw.has_section("voices") or not raw.has_section("general"):
            return
        main = raw.get("general", "voice", fallback="").strip()
        if raw.has_option("general", "voice"):
            raw.remove_option("general", "voice")
        raw["voices"] = {slot: "None" for slot in SLOTS}
        raw.set("voices", "main", main or "Default")

        # quiet.start/end meant "quiet begins/ends"; the Mac names them
        # "stop speaking after" and "start speaking from".
        if raw.has_section("quiet"):
            begin = raw.get("quiet", "start", fallback="")
            end = raw.get("quiet", "end", fallback="")
            if raw.has_option("quiet", "start"):
                raw.remove_option("quiet", "start")
            if raw.has_option("quiet", "end"):
                raw.remove_option("quiet", "end")
            if begin:
                raw.set("quiet", "stop", begin)
            if end:
                raw.set("quiet", "start", end)

        if raw.has_section("alarm"):
            voice = raw.get("alarm", "voice", fallback="").strip()
            if voice:
                raw.set("voices", "alarm", voice)
            for key in ("enabled", "voice"):
                if raw.has_option("alarm", key):
                    raw.remove_option("alarm", key)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("# Steve's Talking Clock - Linux settings\n")
            handle.write("# Edited by the app, but safe to edit by hand.\n\n")
            self._parser.write(handle)
        return self.path

    # -- accessors -------------------------------------------------------

    def get(self, section, key, fallback=""):
        return self._parser.get(section, key, fallback=fallback)

    def set(self, section, key, value):
        if not self._parser.has_section(section):
            self._parser[section] = {}
        self._parser.set(section, key, str(value))

    def get_bool(self, section, key):
        return self.get(section, key).strip().lower() in TRUE

    def get_int(self, section, key, fallback=0):
        try:
            return int(float(self.get(section, key).strip()))
        except (TypeError, ValueError):
            return fallback

    def set_bool(self, section, key, value):
        self.set(section, key, "true" if value else "false")

    # -- voices ----------------------------------------------------------

    def slot_voice(self, slot):
        return self.get("voices", slot, tts_none()).strip() or tts_none()

    def set_slot_voice(self, slot, name):
        self.set("voices", slot, name)

    # -- general ---------------------------------------------------------

    @property
    def voices_dir(self):
        custom = self.get("general", "voices_dir").strip()
        return Path(custom) if custom else default_voices_dir()

    @property
    def hour24(self):
        return self.get_bool("general", "hour24")

    @property
    def gap_ms(self):
        """Gap after each word, in milliseconds. The Mac default is 0.03 s."""
        return max(0, self.get_int("general", "gap_ms", 30))

    @property
    def player(self):
        return self.get("general", "player").strip()

    @property
    def device(self):
        return self.get("general", "device").strip()

    # -- schedule --------------------------------------------------------

    def slots(self):
        """Enabled announcement minutes past the hour."""
        mapping = {"quarter": 15, "half": 30, "three_quarter": 45, "hour": 0}
        return sorted(minute for key, minute in mapping.items())

    def quiet_days(self):
        """Day indexes (Monday = 0) the quiet window applies to."""
        try:
            return parse_days(self.get("quiet", "days"))
        except ValueError:
            # A typo must not silence the quiet hours altogether: fall back to
            # the default of every day and let the settings screens show it.
            return list(range(7))

    def quiet_window(self, day=None):
        """(stop_minutes, start_minutes) or None when that day is not quiet.

        "Stop speaking after" is when quiet begins, "start speaking from" is
        when it ends - the same naming the Mac sheet uses. ``day`` is a weekday
        index with Monday as 0; without it the window is the default every
        listed day follows, which is what the settings screens edit.
        """
        if not self.get_bool("quiet", "enabled"):
            return None
        if day is None:
            return self._quiet_times("")
        if day not in self.quiet_days():
            return None
        name = DAYS[day]
        if self.get_bool("quiet", name + ".off"):
            return None
        return self._quiet_times(name)

    def _quiet_times(self, name):
        """One day's window, with ``mon.stop``/``mon.start`` honoured.

        A day without its own times follows ``quiet.stop``/``quiet.start``, not
        the built-in 11 pm/8 am, or setting the default would change nothing on
        a day that had once been given an override.
        """
        default_stop = parse_clock(self.get("quiet", "stop"), 23 * 60)
        default_start = parse_clock(self.get("quiet", "start"), 8 * 60)
        if not name:
            return default_stop, default_start
        return (
            parse_clock(self.get("quiet", name + ".stop"), default_stop),
            parse_clock(self.get("quiet", name + ".start"), default_start),
        )

    def quiet_week(self):
        """Every day's window, Monday first, or None where nothing is quiet."""
        return [self.quiet_window(index) for index in range(7)]

    def alarm_time(self):
        """Minutes past midnight, or None if the field cannot be parsed."""
        return parse_clock(self.get("alarm", "time"), None)


def tts_none():
    from . import tts

    return tts.NONE


def parse_clock(text, fallback=None):
    """Parse "8:00 am", "08:00", "8pm" into minutes past midnight."""
    text = (text or "").strip().lower().replace(".", ":")
    if not text:
        return fallback

    suffix = ""
    for marker in ("am", "pm"):
        if text.endswith(marker):
            suffix = marker
            text = text[: -len(marker)].strip()
            break

    if ":" in text:
        hour_text, _, minute_text = text.partition(":")
    else:
        hour_text, minute_text = text, "0"
    try:
        hour = int(hour_text)
        minute = int(minute_text or 0)
    except ValueError:
        return fallback

    if suffix == "pm" and hour < 12:
        hour += 12
    if suffix == "am" and hour == 12:
        hour = 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return fallback
    return hour * 60 + minute


def format_clock(minutes, use24=False):
    """Minutes past midnight as "11:00 pm", or "23:00" in 24-hour mode."""
    hour, minute = divmod(int(minutes), 60)
    if use24:
        return "%02d:%02d" % (hour, minute)
    return "%02d:%02d %s" % (hour % 12 or 12, minute, "pm" if hour >= 12 else "am")


def _day_index(word):
    for position, name in enumerate(DAYS):
        if word in (name, name + "day", name + "s"):
            return position
    return {"tues": 1, "weds": 2, "thur": 3, "thurs": 3}.get(word)


def parse_days(text):
    """Day indexes for a ``quiet.days`` value, Monday first.

    Accepts ``every``/``daily``, ``weekdays``, ``weekends``, ``none``, or a
    comma-separated list such as ``mon,tue,wed,thu``. Raises ValueError on a
    word it does not know, so a typo is reported rather than guessed at.
    """
    text = (text or "").strip().lower().replace(" ", "")
    if not text or text in ("every", "everyday", "daily", "all"):
        return list(range(7))
    if text in ("none", "never", "off"):
        return []
    if text in ("weekdays", "weekday", "workdays", "mon-fri", "monfri"):
        return [0, 1, 2, 3, 4]
    if text in ("weekends", "weekend", "sat-sun", "satsun"):
        return [5, 6]
    found = []
    for word in re.split(r"[,;]", text):
        if not word:
            continue
        index = _day_index(word)
        if index is None:
            raise ValueError("unknown day %r" % word)
        if index not in found:
            found.append(index)
    if not found:
        raise ValueError("no days named")
    return sorted(found)


def describe_days(indexes):
    """A short phrase for a set of day indexes, e.g. "Sun to Thu"."""
    indexes = sorted(set(indexes))

    def label(index):
        return DAY_LABELS[DAYS[index]]

    if not indexes:
        return "no days"
    if len(indexes) == 7:
        return "every day"
    if len(indexes) == 1:
        return label(indexes[0])
    if len(indexes) == 2:
        return "%s and %s" % (label(indexes[0]), label(indexes[1]))

    runs, current = [], [indexes[0]]
    for index in indexes[1:]:
        if index == current[-1] + 1:
            current.append(index)
        else:
            runs.append(current)
            current = [index]
    runs.append(current)

    if len(runs) == 2 and runs[0][0] == 0 and runs[-1][-1] == 6:
        # Sunday to Thursday wraps past the end of the week.
        start, end = runs[-1][0], runs[0][-1]
    elif len(runs) == 1:
        start, end = runs[0][0], runs[0][-1]
    else:
        return ", ".join(label(index) for index in indexes)
    return "%s to %s" % (label(start), label(end))
