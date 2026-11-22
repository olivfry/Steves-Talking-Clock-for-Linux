"""Ties a voice, the clock and the audio pipeline together.

Mirrors the Mac build's rules exactly:

* one voice per slot - Main, Quarter Hour, Half Hour, Three Quarter Hour,
  On the Hour and Alarm - each chosen from one list of ``None``, voice packs,
  and speech-synthesis entries;
* automatic announcements never fall back to the main voice; a slot set to
  ``None`` is simply silent;
* the alarm *does* fall back to the main voice when its own slot is ``None``,
  and bypasses quiet mode.
"""

from __future__ import annotations

import datetime

from . import audio, packs, tts
from .config import SLOTS


class EngineError(Exception):
    """Raised when the time cannot be announced."""


def time_text(when, use24):
    """The sentence spoken by speech synthesis.

    Word for word what the Mac build says: "The time is 3 oh 5 P M". Minutes
    under ten are prefixed with "oh", and AM/PM is spelled out with spaces so
    synthesisers read it as two letters rather than one word.
    """
    hour = when.hour
    is_pm = hour >= 12
    if not use24:
        hour = 12 if hour % 12 == 0 else hour % 12

    if when.minute == 0:
        minutes = "o'clock"
    elif when.minute < 10:
        minutes = "oh %d" % when.minute
    else:
        minutes = "%d" % when.minute

    text = "The time is %d %s" % (hour, minutes)
    if not use24:
        text += " P M" if is_pm else " A M"
    return text


def voice_for(config, slot):
    """The voice that slot should use, applying the Mac's alarm fallback."""
    name = config.slot_voice(slot)
    if slot == "alarm" and name == tts.NONE:
        return config.slot_voice("main")
    return name


def voice_kind(entry):
    """"none", "tts" or "pack" for a picker entry."""
    if not entry or entry == tts.NONE:
        return "none"
    if tts.is_tts(entry):
        return "tts"
    return "pack"


def voice_choices(config):
    """The picker, in the Mac build's order: None, Default, then A-Z."""
    names = [pack.name for pack in packs.discover(config.voices_dir)]
    names.append(tts.SYSTEM_VOICE)
    if tts.engine_name():
        names.extend(tts.SYSTEM_PREFIX + voice for voice in tts.available_voices())
    ordered = sorted(set(names), key=str.lower)
    if tts.DEFAULT in ordered:
        ordered.remove(tts.DEFAULT)
        ordered.insert(0, tts.DEFAULT)
    ordered.insert(0, tts.NONE)
    return ordered


def resolve_pack(config, name):
    """The pack called ``name``, or None."""
    if not name or voice_kind(name) != "pack":
        return None
    return packs.find(config.voices_dir, name)


def files_for(pack, when, use24, slot="main"):
    """Resolved file paths for announcing ``when``, plus any missing names."""
    names = packs.announcement_files(pack, when, use24)
    if slot == "alarm" and pack.has("alarm.wav"):
        names = names + ["alarm.wav"]
    paths, missing = [], []
    for name in names:
        path = pack.resolve(name)
        if path:
            paths.append(path)
        else:
            missing.append(name)
    return paths, missing


def validate_selected(config, use24=None):
    """Check every distinct pack currently selected.

    Returns a list of (slot names, pack name, Validation or None).
    """
    use24 = config.hour24 if use24 is None else use24
    grouped = {}
    for slot in SLOTS:
        name = voice_for(config, slot)
        if voice_kind(name) != "pack":
            continue
        grouped.setdefault(name.lower(), (name, []))[1].append(slot)

    reports = []
    for _, (name, slots) in sorted(grouped.items()):
        pack = resolve_pack(config, name)
        reports.append((slots, name, packs.Validation(pack, use24) if pack else None))
    return reports


def _speak_now(config, when, slot, to_file, log):
    name = voice_for(config, slot)
    kind = voice_kind(name)

    if kind == "none":
        return "silent (voice set to %s)" % tts.NONE

    if kind == "tts":
        text = time_text(when, config.hour24)
        if to_file:
            raise EngineError("speech synthesis cannot be rendered to a file - use a voice pack")
        return 'spoken "%s" with %s' % (text, tts.speak(text, name))

    pack = resolve_pack(config, name)
    if pack is None:
        if log:
            log("warning: voice %r is not installed" % name)
        return "silent (voice %r not found)" % name

    paths, missing = files_for(pack, when, config.hour24, slot=slot)
    if missing and log:
        log(
            "warning: %s is missing %s - announcement will be incomplete"
            % (pack.name, ", ".join(sorted(set(missing))))
        )
    if not paths:
        raise EngineError(
            "method %d says nothing at %02d:%02d" % (pack.method(), when.hour, when.minute)
        )

    if to_file:
        audio.render(paths, to_file, gap_ms=config.gap_ms)
        return to_file

    used = audio.play_files(
        paths, gap_ms=config.gap_ms, override=config.player, device=config.device
    )
    return "played %d file(s) with %s from %s" % (len(paths), used, pack.name)


def announce(config, when=None, slot="main", voice=None, to_file=None, log=None):
    """Announce the time for ``slot``.

    ``voice`` temporarily overrides that slot's setting and is restored
    afterwards.
    """
    when = when or datetime.datetime.now()
    previous = config.slot_voice(slot)
    if voice is not None:
        config.set_slot_voice(slot, voice)
    try:
        return _speak_now(config, when, slot, to_file, log)
    finally:
        if voice is not None:
            config.set_slot_voice(slot, previous)
