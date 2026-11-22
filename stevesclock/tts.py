"""System speech synthesis.

The Mac build offers "System Voice" (the voice configured in Spoken Content)
and one "System: <name>" entry per installed AVSpeech voice, alongside the
voice packs. Linux has no single speech API, so this layer drives whatever is
installed - Speech Dispatcher first, espeak-ng as a fallback - and exposes the
same two kinds of entry.
"""

from __future__ import annotations

import shutil
import subprocess

#: Special picker entries, spelled exactly as the Mac build spells them.
NONE = "None"
DEFAULT = "Default"
SYSTEM_VOICE = "System Voice"
SYSTEM_PREFIX = "System: "


class TtsError(Exception):
    """Raised when no speech engine can be used."""


#: Speech Dispatcher lists every espeak language crossed with every espeak
#: variant ("Tatar+Robosoft7"), which is thousands of near-duplicates. The
#: picker only offers one entry per language; variants are still reachable by
#: name if you set one yourself.
MAX_VOICES = 250


def _language(name):
    """"Tatar+Robosoft7" -> "Tatar"."""
    return name.split("+", 1)[0].strip()


def engine_name():
    if shutil.which("spd-say"):
        return "speech-dispatcher"
    if shutil.which("espeak-ng"):
        return "espeak-ng"
    return None


def available_voices(variants=False):
    """Best-effort list of installed synthesis voices, one per language."""
    voices = []

    if shutil.which("spd-say"):
        try:
            completed = subprocess.run(
                ["spd-say", "-L"], capture_output=True, text=True, timeout=10, check=False
            )
            for line in completed.stdout.splitlines():
                line = line.strip()
                if not line or line.lower().startswith(("name", "usage")):
                    continue
                name = line.split()[0]
                if variants:
                    if name not in voices:
                        voices.append(name)
                    continue
                base = _language(name)
                if base and base not in voices:
                    voices.append(base)
        except (OSError, subprocess.SubprocessError):
            pass

    if not voices and shutil.which("espeak-ng"):
        try:
            completed = subprocess.run(
                ["espeak-ng", "--voices"], capture_output=True, text=True, timeout=10, check=False
            )
            for line in completed.stdout.splitlines()[1:]:
                fields = line.split()
                if len(fields) < 4:
                    continue
                name = _language(fields[3]) if not variants else fields[3]
                if name and name not in voices:
                    voices.append(name)
        except (OSError, subprocess.SubprocessError):
            pass

    return sorted(voices, key=str.lower)[:MAX_VOICES]


def voice_name(entry):
    """"System: X" -> "X"; "System Voice" -> None (the default voice)."""
    if entry.startswith(SYSTEM_PREFIX):
        return entry[len(SYSTEM_PREFIX) :].strip() or None
    return None


def is_tts(entry):
    return entry == SYSTEM_VOICE or entry.startswith(SYSTEM_PREFIX)


def speak(text, entry=SYSTEM_VOICE):
    """Speak ``text``. Returns a description of the engine used."""
    voice = voice_name(entry)

    if shutil.which("spd-say"):
        command = ["spd-say", "-w"]
        if voice:
            command += ["-y", voice]
        command.append(text)
        subprocess.run(command, check=False)
        return "speech-dispatcher" + (' (voice "%s")' % voice if voice else "")

    if shutil.which("espeak-ng"):
        command = ["espeak-ng"]
        if voice:
            command += ["-v", voice]
        command.append(text)
        subprocess.run(command, check=False)
        return "espeak-ng" + (' (voice "%s")' % voice if voice else "")

    raise TtsError(
        "no speech engine installed - sudo apt install speech-dispatcher "
        "(or espeak-ng), or choose a voice pack instead"
    )
