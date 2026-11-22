"""Configuration and XDG paths.

The settings names follow the Mac build's @AppStorage keys so the two ports
describe the same thing in the same words: one voice per announcement slot, a
gap delay in milliseconds (0.03 s on the Mac), alarm time, and a quiet window
with "stop speaking after" / "start speaking from".
"""

from __future__ import annotations

import configparser
import os
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

DEFAULTS = {
    "general": {
        "voices_dir": "",
        "player": "",
        "device": "",
        "gap_ms": "30",
        "hour24": "false",
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

    def quiet_window(self):
        """(stop_minutes, start_minutes) or None when quiet mode is off.

        "Stop speaking after" is when quiet begins, "start speaking from" is
        when it ends - the same naming the Mac sheet uses.
        """
        if not self.get_bool("quiet", "enabled"):
            return None
        stop = parse_clock(self.get("quiet", "stop"), 23 * 60)
        start = parse_clock(self.get("quiet", "start"), 8 * 60)
        return stop, start

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
