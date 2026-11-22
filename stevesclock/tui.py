"""Terminal menu - what bare ``steves-clock`` opens at a terminal.

Deliberately *not* curses. A full-screen curses UI redraws constantly and
screen readers read the wreckage; this is a plain numbered menu that prints one
thing at a time and keeps a spoken summary on screen, so Orca reads something
useful after every action. It also works fine over SSH.

The controls are the Mac window's: the six voice lists, the gap delay, the
alarm time, 24 Hour Mode, and Say Time / Initialise / Open Voices Folder /
Audio / Quiet Mode.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from . import __version__, audio, engine, packs, tts
from .config import SLOT_LABELS, SLOTS, Config, format_clock, parse_clock

HOTKEY_TEXT = """Global hotkeys need an X11 key grab, which this build does not do.
The command line covers each one:

  Say Time          steves-clock say
  24 Hour Mode      steves-clock set general.hour24=true
  Initialise        steves-clock initialise
  Audio             steves-clock set general.device=<sink>
  Quiet             steves-clock set quiet.enabled=true
  Minimise          leave this menu; the service keeps running

Bind those to keys in your desktop's keyboard settings."""


class _End(Exception):
    """Raised when input runs out."""


def service_state():
    """One line describing whether the clock is running on its own."""
    if not shutil.which("systemctl"):
        return "unknown - no systemd here"
    try:
        completed = subprocess.run(
            ["systemctl", "--user", "is-active", "steves-clock.service"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    state = completed.stdout.strip()
    if state == "active":
        return "active - announces on the quarter hours with this menu closed"
    if state == "inactive" and os.geteuid() == 0:
        # A root install provides a shared unit and an autostart entry; neither
        # runs until one of them is used, so point at the command that does it.
        return "not running here - systemctl --user enable --now steves-clock"
    if state == "inactive":
        return "not running - nothing is announced unless you start it"
    if state == "failed":
        return "failed - see journalctl --user -u steves-clock"
    return state or "unknown"


def have_display():
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


class Menu:
    def __init__(self, config, stdin=None, stdout=None):
        self.config = config
        self.stdin = stdin or sys.stdin
        self.out = stdout or sys.stdout

    # -- io --------------------------------------------------------------

    def say(self, text=""):
        print(text, file=self.out)

    def prompt(self, text):
        self.out.write(text)
        self.out.flush()
        line = self.stdin.readline()
        if not line:
            raise _End
        return line.strip()

    # -- screens ---------------------------------------------------------

    def summary(self):
        config = self.config
        self.say("")
        self.say("Steve's Talking Clock %s" % __version__)
        for slot in SLOTS:
            stored = config.slot_voice(slot)
            note = self.note(config, stored)
            if slot == "alarm" and stored == tts.NONE:
                note = "falls back to the Main Voice"
            self.say("  %-30s %-22s %s" % (SLOT_LABELS[slot] + ":", stored, note))
        self.say("  %-30s %.3fs" % ("Gap Delay:", config.gap_ms / 1000.0))
        self.say("  %-30s %s" % ("Alarm time:", config.get("alarm", "time")))
        self.say("  %-30s %s" % ("24 Hour Mode:", "on" if config.hour24 else "off"))
        quiet = config.quiet_window()
        self.say(
            "  %-30s %s"
            % (
                "Quiet mode:",
                "on, after %s until %s"
                % (format_clock(quiet[0], config.hour24), format_clock(quiet[1], config.hour24))
                if quiet
                else "off",
            )
        )
        self.say("  %-30s %s" % ("Output device:", config.device or "system default"))
        self.say("  %-30s %s" % ("Background service:", service_state()))
        self.say("")

    def note(self, config, name):
        kind = engine.voice_kind(name)
        if kind == "none":
            return "silent"
        if kind == "tts":
            return "speech synthesis" + ("" if tts.engine_name() else " (no engine)")
        pack = engine.resolve_pack(config, name)
        if pack is None:
            return "not installed"
        return "method %d - %s" % (pack.method(), packs.METHOD_NAMES.get(pack.method(), ""))

    def run(self):
        actions = [
            ("1", "Main Voice List", lambda: self.choose_voice("main")),
            ("2", "Quarter Hour Voice List", lambda: self.choose_voice("quarter")),
            ("3", "Half Hour Voice List", lambda: self.choose_voice("half")),
            ("4", "Three Quarter Hour Voice List", lambda: self.choose_voice("three_quarter")),
            ("5", "On the Hour Voice List", lambda: self.choose_voice("hour")),
            ("6", "Alarm Voice List", lambda: self.choose_voice("alarm")),
            ("7", "Gap Delay", self.choose_gap),
            ("8", "Alarm time", self.choose_alarm_time),
            ("9", "24 Hour Mode (toggle)", self.toggle_24),
            ("S", "Say Time", self.say_time),
            ("I", "Initialise", self.initialise),
            ("O", "Voices folder: path and packs", self.open_folder),
            ("P", "Add a voice pack from a folder", self.add_pack),
            ("U", "Update (check GitHub and install)", self.update),
            ("A", "Audio output device", self.choose_device),
            ("Q", "Quiet Mode", self.quiet_menu),
            ("K", "Hotkeys", self.hotkeys),
            ("X", "Exit", None),
        ]

        self.summary()

        while True:
            self.say("Menu:")
            for key, label, _ in actions:
                self.say("  %s) %s" % (key, label))
            try:
                answer = self.prompt("Choice: ")
            except _End:
                self.say("")
                return 0
            if not answer:
                continue
            for key, _label, handler in actions:
                if answer.lower() == key.lower():
                    if handler is None:
                        self.say("")
                        self.say("Menu closed. Background service: %s" % service_state())
                        return 0
                    handler()
                    break
            else:
                self.say("No such choice: %s" % answer)
            self.summary()

    # -- actions ---------------------------------------------------------

    def choose_voice(self, slot):
        config = self.config
        choices = engine.voice_choices(config)
        current = config.slot_voice(slot)
        self.say("")
        self.say("%s - current: %s" % (SLOT_LABELS[slot], current))
        for index, entry in enumerate(choices, 1):
            self.say(
                "  %s%3d) %-30s %s"
                % ("*" if entry == current else " ", index, entry, self.note(config, entry))
            )
        try:
            answer = self.prompt("Number, voice name, or blank to cancel: ")
        except _End:
            return
        if not answer:
            return
        if answer.isdigit():
            index = int(answer)
            if not 1 <= index <= len(choices):
                self.say("No entry %d." % index)
                return
            chosen = choices[index - 1]
        else:
            chosen = answer
        config.set_slot_voice(slot, chosen)
        config.save()
        self.say("%s set to %s." % (SLOT_LABELS[slot], chosen))

    def choose_gap(self):
        config = self.config
        try:
            answer = self.prompt(
                "Gap Delay in seconds, 0.000 to 0.200 [%.3f]: " % (config.gap_ms / 1000.0)
            )
        except _End:
            return
        if not answer:
            return
        try:
            seconds = float(answer)
        except ValueError:
            self.say("That is not a number.")
            return
        if not 0.0 <= seconds <= 0.2:
            self.say("Keep it between 0.000 and 0.200 seconds.")
            return
        config.set("general", "gap_ms", int(round(seconds * 1000)))
        config.save()
        self.say("Gap Delay set to %.3fs." % seconds)

    def choose_alarm_time(self):
        config = self.config
        try:
            answer = self.prompt(
                "Alarm time, hh:mm am or hh:mm [%s]: " % config.get("alarm", "time")
            )
        except _End:
            return
        if not answer:
            return
        minutes = parse_clock(answer)
        if minutes is None:
            self.say("Could not read %r - try 7:30 am or 07:30." % answer)
            return
        config.set("alarm", "time", format_clock(minutes, config.hour24))
        config.save()
        self.say("Alarm time set to %s." % config.get("alarm", "time"))

    def toggle_24(self):
        config = self.config
        config.set_bool("general", "hour24", not config.hour24)
        # Same as the Mac: rewrite the alarm time into the new format.
        minutes = config.alarm_time()
        if minutes is not None:
            config.set("alarm", "time", format_clock(minutes, config.hour24))
        config.save()
        self.say("24 Hour Mode %s." % ("on" if config.hour24 else "off"))

    def say_time(self):
        try:
            self.say(engine.announce(self.config, slot="main", log=self.say))
        except Exception as error:
            self.say("Could not play: %s" % error)

    def initialise(self):
        config = self.config
        installed = packs.discover(config.voices_dir)
        self.say("")
        self.say("Voices folder: %s" % config.voices_dir)
        self.say("Found %d pack(s): %s" % (
            len(installed), ", ".join(pack.name for pack in installed) or "none"))
        reports = engine.validate_selected(config)
        if not reports:
            self.say("No voice packs are selected.")
            return
        for slots, name, validation in reports:
            if validation is None:
                self.say("%s (used by %s): MISSING from the voices folder" % (name, ", ".join(slots)))
            elif validation.ok:
                self.say("%s (used by %s): OK, method %d, %d files"
                         % (name, ", ".join(slots), validation.method, len(validation.pack)))
            else:
                self.say("%s (used by %s): missing %s"
                         % (name, ", ".join(slots), ", ".join(item[0] for item in validation.missing)))

    def open_folder(self):
        """Print the folder and its packs.

        A file manager is only launched when there is a display; on a headless
        box xdg-open either fails or starts something nobody can see, and the
        path plus a pack listing is what is actually useful there.
        """
        folder = self.config.voices_dir
        folder.mkdir(parents=True, exist_ok=True)
        self.say("")
        self.say("Voices folder: %s" % folder)
        installed = packs.discover(folder)
        if not installed:
            self.say("No packs installed yet.")
        else:
            self.say("%d pack(s):" % len(installed))
            for pack in installed:
                self.say(
                    "  %-24s method %-2d %-28s %d files"
                    % (
                        pack.name,
                        pack.method(),
                        packs.METHOD_NAMES.get(pack.method(), ""),
                        len(pack),
                    )
                )
        self.say("Add one with P, or copy a folder in there yourself.")
        if have_display():
            try:
                subprocess.Popen(["xdg-open", str(folder)])
            except OSError as error:
                self.say("Could not open a file manager: %s" % error)
        else:
            self.say("(No graphical display here, so no file manager was opened.)")

    def update(self):
        # Imported here: cli imports this module for the menu, so a top-level
        # import would be circular.
        from types import SimpleNamespace

        from .cli import cmd_update

        self.say("")
        try:
            cmd_update(self.config, SimpleNamespace(check=False))
        except Exception as error:  # never kill the menu over a failed update
            self.say("Update failed: %s" % error)
        self.say("")

    def add_pack(self):
        try:
            answer = self.prompt("Folder containing the .wav files, or blank to cancel: ")
        except _End:
            return
        if not answer:
            return
        source = os.path.abspath(os.path.expanduser(answer.strip().strip("'\"")))
        if not os.path.isdir(source):
            self.say("Not a folder: %s" % source)
            return
        try:
            pack = packs.Pack(source)
        except packs.PackError as error:
            self.say(str(error))
            return
        destination = self.config.voices_dir / pack.name
        self.config.voices_dir.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            self.say("%s already exists - copy it somewhere else, or delete it first." % destination)
            return
        shutil.copytree(source, destination)
        self.config.set_slot_voice("main", pack.name)
        self.config.save()
        self.say(
            "Installed %s (method %d, %d files) and set it as the Main Voice."
            % (pack.name, pack.method(), len(pack))
        )

    def choose_device(self):
        config = self.config
        sinks = audio.list_sinks()
        self.say("")
        self.say("  1) System Default")
        for index, sink in enumerate(sinks, 2):
            self.say("%s%2d) %s" % ("*" if sink == config.device else " ", index, sink))
        if not sinks:
            self.say("No devices listed - install pulseaudio-utils or pipewire for a list.")
        try:
            answer = self.prompt("Number, device name, or blank to cancel: ")
        except _End:
            return
        if not answer:
            return
        if answer.isdigit():
            index = int(answer)
            if index == 1:
                device = ""
            elif 2 <= index <= len(sinks) + 1:
                device = sinks[index - 2]
            else:
                self.say("No entry %d." % index)
                return
        else:
            device = answer
        config.set("general", "device", device)
        config.save()
        self.say("Output device set to %s." % (device or "system default"))

    def quiet_menu(self):
        config = self.config
        while True:
            quiet = config.quiet_window()
            self.say("")
            self.say("Quiet Mode Settings")
            self.say("  Quiet mode:            %s" % ("on" if quiet else "off"))
            self.say("  Stop speaking after:   %s" % config.get("quiet", "stop"))
            self.say("  Start speaking from:   %s" % config.get("quiet", "start"))
            self.say("  1) Enable           2) Disable")
            self.say("  3) Stop speaking after    4) Start speaking from")
            self.say("  5) Back")
            try:
                answer = self.prompt("Choice: ")
            except _End:
                return
            if answer == "1":
                config.set_bool("quiet", "enabled", True)
            elif answer == "2":
                config.set_bool("quiet", "enabled", False)
            elif answer in ("3", "4"):
                key = "stop" if answer == "3" else "start"
                label = "Stop speaking after" if key == "stop" else "Start speaking from"
                value = self.prompt("%s [%s]: " % (label, config.get("quiet", key)))
                if value:
                    if parse_clock(value) is None:
                        self.say("Could not read %r - try 11:00 pm or 23:00." % value)
                        continue
                    config.set("quiet", key, value)
            elif answer in ("5", ""):
                return
            else:
                self.say("No such choice: %s" % answer)
                continue
            config.save()

    def hotkeys(self):
        self.say("")
        for line in HOTKEY_TEXT.splitlines():
            self.say(line)


def main(argv=None):
    config = Config()
    return Menu(config).run()


if __name__ == "__main__":
    sys.exit(main())
