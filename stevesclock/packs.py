"""Voice-pack detection, word lists and validation.

This is a port of the macOS implementation (ContentView.swift) so that both
ports read the same ``concatenation.txt``-style voice folders. Where the two
disagree, the Mac behaviour wins: a pack that works there must work here.
"""

from __future__ import annotations

import os

#: Prefixes used to build 13-19 from a stem plus "teen".
TEENS = ("thir", "for", "fif", "six", "seven", "eight", "nine")

#: Stems for the tens 20-50.
TENS = ("twen", "thir", "for", "fif")

#: Sound-only methods. They never speak AM/PM and ignore 24-hour mode.
CHIMING = frozenset({6, 7, 8})

#: Optional files listed in concatenation.txt. Never required, only reported.
OPTIONAL = ("h.wav", "m.wav", "announcement.wav", "hour1.wav", "hour2.wav", "alarm.wav")

#: Every method the format defines, in spec order.
METHODS = range(1, 11)

#: One-line descriptions, used by ``steves-clock list`` and validation reports.
METHOD_NAMES = {
    1: "Simple",
    2: "Traditional talking clock",
    3: "Digits only",
    4: "Individual 1-59 (most natural)",
    5: "Natural, minute variants (1a-12a)",
    6: "Big Ben",
    7: "Cuckoo",
    8: "Hourly sounds",
    9: "Natural, a-variants throughout",
    10: "Danish (one and twenty)",
}


class PackError(Exception):
    """Raised when a folder cannot be used as a voice pack."""


class Pack:
    """A voice folder, with case-insensitive file lookup.

    Packs built on Windows carry ``AM.wav`` and ``PM.wav``. That works on
    macOS, whose filesystem is case-insensitive by default, but a naive
    ``am.wav`` lookup fails on Linux. Every lookup here goes through a
    lower-cased index built once at load time.
    """

    def __init__(self, path):
        self.path = os.path.abspath(path)
        if not os.path.isdir(self.path):
            raise PackError("not a directory: %s" % self.path)
        self._files = {}
        for name in sorted(os.listdir(self.path)):
            if name.lower().endswith(".wav"):
                self._files.setdefault(name.lower(), name)
        if not self._files:
            raise PackError("no .wav files in %s" % self.path)

    # -- introspection ----------------------------------------------------

    @property
    def name(self):
        return os.path.basename(self.path.rstrip(os.sep))

    def __len__(self):
        return len(self._files)

    def __repr__(self):
        return "Pack(%r, method=%d, %d files)" % (self.path, self.method(), len(self))

    def has(self, filename):
        return filename.lower() in self._files

    def resolve(self, filename):
        """Absolute path for ``filename``, or None if the pack lacks it."""
        actual = self._files.get(filename.lower())
        return os.path.join(self.path, actual) if actual else None

    def method(self):
        """Detect the concatenation method from the files present.

        Order matters: the most specific markers are tested first, exactly as
        in the Mac port and as concatenation.txt requires.
        """
        checks = (
            ("cuckoo.wav", 7),
            ("chime.wav", 6),
            ("hour01.wav", 8),
            ("and.wav", 10),
        )
        for filename, method in checks:
            if self.has(filename):
                return method
        if self.has("teena.wav") or self.has("tya.wav") or self.has("ty1a.wav"):
            return 9
        if self.has("1a.wav"):
            return 5
        if self.has("thir.wav"):
            return 2
        if self.has("59.wav"):
            return 4
        if self.has("10.wav"):
            return 1
        if self.has("its.wav") and self.has("1.wav"):
            return 3
        return 1


# -- word lists -----------------------------------------------------------


def build_word_list(method, hour24, minute, use24):
    """File names for one announcement, or [] if this method is silent now.

    ``hour24`` is the raw 0-23 hour; ``use24`` selects the spoken hour form.
    Chiming methods (6/7/8) ignore ``use24`` at the call site.
    """
    hour12 = hour24 % 12 or 12
    hour = hour24 if use24 else hour12
    words = []

    # -- hour ------------------------------------------------------------
    if method == 1:
        if hour <= 20:
            words.append("%d.wav" % hour)
        else:
            words += ["20.wav", "%d.wav" % (hour % 10)]

    elif method in (2, 5, 9):
        if hour <= 12:
            words.append("%d.wav" % hour)
        elif hour < 20:
            words += ["%s.wav" % TEENS[hour - 13], "teen.wav"]
        else:
            words += ["twen.wav", "ty.wav"]
            if hour % 10:
                words.append("ty%d.wav" % (hour % 10))

    elif method == 3:
        words += ["%s.wav" % digit for digit in str(hour)]

    elif method == 4:
        words.append("%d.wav" % hour)

    elif method == 6:
        return {0: ["00.wav"], 15: ["15.wav"], 30: ["30.wav"], 45: ["45.wav"]}.get(minute, [])

    elif method == 7:
        return ["cuckoo.wav"] * hour12

    elif method == 8:
        return ["hour%02d.wav" % hour12]

    elif method == 10:
        if hour <= 20:
            words.append("%d.wav" % hour)
        else:
            words += ["%d.wav" % (hour % 10), "and.wav", "20.wav"]

    else:
        words.append("%d.wav" % hour)

    # -- minute ----------------------------------------------------------
    if method == 1:
        if minute == 0:
            words.append("00.wav")
        elif minute < 10:
            words += ["o.wav", "%d.wav" % minute]
        elif minute < 20:
            words.append("%d.wav" % minute)
        else:
            words.append("%d.wav" % (minute // 10 * 10))
            if minute % 10:
                words.append("%d.wav" % (minute % 10))

    elif method == 2:
        if minute == 0:
            words.append("00.wav")
        elif minute < 10:
            words += ["o.wav", "%d.wav" % minute]
        elif minute < 13:
            words.append("%d.wav" % minute)
        elif minute < 20:
            words += ["%s.wav" % TEENS[minute - 13], "teen.wav"]
        else:
            words += ["%s.wav" % TENS[minute // 10 - 2], "ty.wav"]
            if minute % 10:
                words.append("ty%d.wav" % (minute % 10))

    elif method == 3:
        if minute == 0:
            words.append("00.wav")
        elif minute < 10:
            words += ["o.wav", "%d.wav" % minute]
        else:
            words += ["%s.wav" % digit for digit in str(minute)]

    elif method == 4:
        words.append("00.wav" if minute == 0 else "%d.wav" % minute)

    elif method == 5:
        # 1a-12a cover the minutes; 13-59 reuse the hour recordings.
        if minute == 0:
            words.append("00.wav")
        elif minute < 10:
            words += ["o.wav", "%da.wav" % minute]
        elif minute < 13:
            words.append("%da.wav" % minute)
        elif minute < 20:
            words += ["%s.wav" % TEENS[minute - 13], "teen.wav"]
        else:
            words += ["%s.wav" % TENS[minute // 10 - 2], "ty.wav"]
            if minute % 10:
                words.append("ty%d.wav" % (minute % 10))

    elif method == 9:
        if minute == 0:
            words.append("00.wav")
        elif minute < 10:
            words += ["o.wav", "%da.wav" % minute]
        elif minute < 13:
            words.append("%da.wav" % minute)
        elif minute < 20:
            words += ["%s.wav" % TEENS[minute - 13], "teena.wav"]
        else:
            words += ["%s.wav" % TENS[minute // 10 - 2], "tya.wav"]
            if minute % 10:
                words.append("ty%da.wav" % (minute % 10))

    elif method == 10:
        if minute < 20 or minute % 10 == 0:
            words.append("%d.wav" % minute)
        else:
            words += ["%d.wav" % (minute % 10), "and.wav", "%d.wav" % (minute // 10 * 10)]

    return words


def announcement_files(pack, when, use24):
    """Full file-name list for announcing ``when`` with ``pack``.

    Adds the "It's" prefix and the AM/PM suffix for talking methods only;
    chiming clocks play their sound and nothing else.
    """
    method = pack.method()
    effective_use24 = False if method in CHIMING else use24
    words = build_word_list(method, when.hour, when.minute, effective_use24)
    if not words:
        return []
    if method not in CHIMING:
        words.insert(0, "its.wav")
        if not effective_use24:
            words.append("pm.wav" if when.hour >= 12 else "am.wav")
    return words


# -- validation -----------------------------------------------------------


def required_files(method, use24):
    """Map of required file name -> one (hour, minute) that needs it.

    Derived from build_word_list rather than a second hand-written table, so
    the two can never drift apart.
    """
    needed = {}
    for hour in range(24):
        for minute in range(60):
            words = build_word_list(method, hour, minute, use24)
            if not words:
                continue
            if method not in CHIMING:
                words = ["its.wav"] + words
                if not use24:
                    words = words + ["pm.wav" if hour >= 12 else "am.wav"]
            for name in words:
                needed.setdefault(name, (hour, minute))
    return needed


class Validation:
    """Result of checking a pack against the format spec."""

    def __init__(self, pack, use24):
        self.pack = pack
        self.use24 = use24
        self.method = pack.method()
        # Chiming methods behave as 12-hour clocks whatever the setting.
        self.effective_use24 = False if self.method in CHIMING else use24
        needed = required_files(self.method, self.effective_use24)
        self.missing = []
        for name, (hour, minute) in sorted(needed.items()):
            if not pack.has(name):
                self.missing.append((name, hour, minute))
        self.present_optional = [n for n in OPTIONAL if pack.has(n)]
        self.case_only = []
        for name, _, _ in self.missing:
            actual = pack.resolve(name)
            if actual:
                self.case_only.append((name, os.path.basename(actual)))

    @property
    def ok(self):
        return not self.missing

    def report(self):
        mode = "24-hour" if self.effective_use24 else "12-hour"
        lines = [
            "Pack:    %s" % self.pack.name,
            "Folder:  %s" % self.pack.path,
            "Files:   %d .wav" % len(self.pack),
            "Method:  %d - %s" % (self.method, METHOD_NAMES.get(self.method, "unknown")),
            "Mode:    %s" % mode,
            "",
        ]
        if self.method in CHIMING:
            lines.append("Note: this is a sound-only clock; 24-hour mode does not apply.")
            lines.append("")
        if self.ok:
            lines.append("OK: every file the spec requires is present.")
        else:
            lines.append("MISSING %d file(s):" % len(self.missing))
            for name, hour, minute in self.missing:
                lines.append("  %-14s needed at %02d:%02d" % (name, hour, minute))
            lines.append("")
            lines.append("The clock skips files it cannot find, so announcements using")
            lines.append("these will be short or wrong.")
        if self.present_optional:
            lines.append("")
            lines.append("Optional files present: %s" % ", ".join(self.present_optional))
        return "\n".join(lines)


def load_pack(path):
    return Pack(path)


def discover(voices_dir):
    """Every usable pack inside ``voices_dir``, sorted by name.

    A folder that holds .wav files directly counts as a pack in its own right,
    so pointing the voices setting straight at a pack also works.
    """
    packs = []
    if not os.path.isdir(voices_dir):
        return packs
    try:
        entries = sorted(os.listdir(voices_dir))
    except OSError:
        return packs
    if any(name.lower().endswith(".wav") for name in entries):
        try:
            packs.append(Pack(voices_dir))
        except PackError:
            pass
    for name in entries:
        path = os.path.join(voices_dir, name)
        if not os.path.isdir(path):
            continue
        try:
            packs.append(Pack(path))
        except PackError:
            continue
    return packs


def find(voices_dir, name):
    """Look up a pack by name, case-insensitively. Returns None if absent."""
    for pack in discover(voices_dir):
        if pack.name.lower() == (name or "").lower():
            return pack
    return None
