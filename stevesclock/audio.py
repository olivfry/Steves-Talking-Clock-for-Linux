"""Gapless playback of word files.

The Mac build mixes every word into one buffer, inserting ``gapDuration``
silence after each file and 0.3 s at the end. Playing files one ``paplay`` at a
time here would put a much bigger gap at every boundary, so this module
concatenates the PCM into a single WAV first and plays that, with the same gap
and tail the Mac uses.

Everything is mixed to 16-bit signed. Sample rates and channel counts are
normalised to the widest input in the list.
"""

from __future__ import annotations

import array
import os
import shutil
import subprocess
import sys
import tempfile
import wave

#: Players we know how to drive, most preferred first.
PLAYERS = (
    ("paplay", ("paplay",)),
    ("pw-play", ("pw-play",)),
    ("aplay", ("aplay", "-q")),
    ("ffplay", ("ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet")),
)

#: Speech fallbacks, used when a TTS voice is selected.
SPEAKERS = (("spd-say", ("spd-say", "-w")), ("espeak-ng", ("espeak-ng",)))

#: Trailing silence after an announcement, in milliseconds, as on the Mac.
TAIL_MS = 300


class AudioError(Exception):
    """Raised when sound cannot be produced or decoded."""


def _blank(count):
    return array.array("h", [0]) * count


def _to_int16(raw, sampwidth):
    if sampwidth == 2:
        samples = array.array("h")
        samples.frombytes(raw)
        if sys.byteorder == "big":
            samples.byteswap()
        return samples
    if sampwidth == 1:
        samples = _blank(len(raw))
        for index, value in enumerate(raw):
            samples[index] = (value - 128) << 8
        return samples
    if sampwidth == 3:
        count = len(raw) // 3
        samples = _blank(count)
        for index in range(count):
            b0, b1, b2 = raw[3 * index : 3 * index + 3]
            value = b0 | (b1 << 8) | (b2 << 16)
            if value & 0x800000:
                value -= 0x1000000
            samples[index] = value >> 8
        return samples
    if sampwidth == 4:
        wide = array.array("i")
        wide.frombytes(raw)
        if sys.byteorder == "big":
            wide.byteswap()
        samples = _blank(len(wide))
        for index, value in enumerate(wide):
            samples[index] = value >> 16
        return samples
    raise AudioError("unsupported sample width: %d bytes" % sampwidth)


def load(path):
    """Return (samples, rate, channels) for a WAV file."""
    try:
        with wave.open(path, "rb") as handle:
            channels = handle.getnchannels()
            sampwidth = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.getnframes()
            raw = handle.readframes(frames)
    except (wave.Error, EOFError, OSError) as error:
        raise AudioError("%s: not a readable WAV (%s)" % (os.path.basename(path), error))
    if channels not in (1, 2):
        raise AudioError("%s: %d channels is not supported" % (os.path.basename(path), channels))
    return _to_int16(raw, sampwidth), rate, channels


def _conform(samples, rate, channels, target_rate, target_channels):
    """Resample and remix one file to the target format."""
    if channels != target_channels:
        if target_channels == 2:
            mixed = _blank(len(samples) * 2)
            for index, value in enumerate(samples):
                mixed[2 * index] = value
                mixed[2 * index + 1] = value
            samples = mixed
        else:
            frames = len(samples) // 2
            mixed = _blank(frames)
            for index in range(frames):
                mixed[index] = (samples[2 * index] + samples[2 * index + 1]) // 2
            samples = mixed

    if rate != target_rate:
        frames = len(samples) // target_channels
        new_frames = max(1, int(frames * target_rate / rate))
        out = _blank(new_frames * target_channels)
        ratio = frames / new_frames
        for frame in range(new_frames):
            source = frame * ratio
            low = int(source)
            high = min(low + 1, frames - 1)
            frac = source - low
            for channel in range(target_channels):
                a = samples[low * target_channels + channel]
                b = samples[high * target_channels + channel]
                out[frame * target_channels + channel] = int(a + (b - a) * frac)
        samples = out

    return samples


def render(paths, out_path, gap_ms=30, tail_ms=TAIL_MS):
    """Concatenate ``paths`` into one 16-bit WAV.

    A gap of ``gap_ms`` follows every file, including the last, and then
    ``tail_ms`` of silence is appended - the same shape as the Mac build.
    """
    if not paths:
        raise AudioError("nothing to play")
    loaded = [load(path) for path in paths]
    target_rate = max(rate for _, rate, _ in loaded)
    target_channels = max(channels for _, _, channels in loaded)

    gap = None
    if gap_ms > 0:
        gap = _blank(int(target_rate * gap_ms / 1000) * target_channels)

    chunks = []
    for samples, rate, channels in loaded:
        samples = _conform(samples, rate, channels, target_rate, target_channels)
        chunks.append(samples)
        if gap is not None:
            chunks.append(gap)

    if tail_ms > 0:
        chunks.append(_blank(int(target_rate * tail_ms / 1000) * target_channels))

    with wave.open(out_path, "wb") as handle:
        handle.setnchannels(target_channels)
        handle.setsampwidth(2)
        handle.setframerate(target_rate)
        handle.writeframes(b"".join(chunk.tobytes() for chunk in chunks))
    return out_path


def duration(path):
    """Length of a WAV in seconds, without decoding its samples."""
    with wave.open(path, "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


def list_sinks():
    """Output devices, best effort, for the Audio dialog."""
    if not shutil.which("pactl"):
        return []
    try:
        completed = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    sinks = []
    for line in completed.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) >= 2 and fields[1].strip():
            sinks.append(fields[1].strip())
    return sinks


def player_command(override="", device=""):
    """Command list for the player, including the output device if given."""
    if override:
        base = tuple(override.split())
    else:
        base = None
        for _, command in PLAYERS:
            if shutil.which(command[0]):
                base = command
                break
        if base is None:
            return None

    device_option = ()
    if device:
        name = base[0]
        if name == "paplay":
            device_option = ("--device=" + device,)
        elif name == "pw-play":
            device_option = ("--target=" + device,)
        elif name == "aplay":
            device_option = ("-D", device)

    return tuple(base) + device_option


def play(path, override="", device=""):
    """Play a file, blocking until it finishes. Returns the command used."""
    command = player_command(override, device)
    if command is None:
        raise AudioError(
            "no audio player found - install one with: "
            "sudo apt install pulseaudio-utils  (or pipewire-bin, or alsa-utils)"
        )
    result = subprocess.run(list(command) + [path], check=False)
    if result.returncode != 0:
        raise AudioError("%s exited with status %d" % (command[0], result.returncode))
    return " ".join(command)


def play_files(paths, gap_ms=30, override="", device=""):
    """Render ``paths`` to a temporary file and play it."""
    handle = tempfile.NamedTemporaryFile(prefix="steves-clock-", suffix=".wav", delete=False)
    handle.close()
    try:
        render(paths, handle.name, gap_ms=gap_ms)
        return play(handle.name, override=override, device=device)
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
