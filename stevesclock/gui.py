"""GTK 3 window, laid out like the Mac build.

Left column: the six voice lists, the Gap Delay slider, the alarm time and the
24 Hour Mode toggle. Right column: Say Time, Initialise, Open Voices Folder,
Save Settings, Audio…, Hotkeys…, Quiet Mode…, Minimise.

Settings save as you change them, exactly as @AppStorage does on the Mac;
"Save Settings" is kept for parity and beeps.
"""

from __future__ import annotations

import subprocess
import sys

from . import audio, engine, packs, tts
from .config import SLOT_LABELS, SLOTS, Config, format_clock, parse_clock

_window = None


def main(argv=None):
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
    except (ImportError, ValueError) as error:
        print("error: GTK 3 is not available (%s)" % error, file=sys.stderr)
        print("install it with: sudo apt install python3-gi gir1.2-gtk-3.0", file=sys.stderr)
        return 1

    # Gtk.Window raises a bare RuntimeError when there is no display, which is
    # what happens over SSH or from a tty; say so instead of dumping a trace.
    try:
        ready = Gtk.init_check(sys.argv)[0]
    except AttributeError:  # pragma: no cover - GTK 3 always has it
        ready = True
    if not ready:
        print("error: no graphical display was found.", file=sys.stderr)
        print("Use 'steves-clock' for the menu, or start this from your desktop.", file=sys.stderr)
        return 1

    global _window
    _window = SettingsWindow(Gtk, Config())
    try:
        Gtk.main()
    except KeyboardInterrupt:
        # Ctrl-C in the launching terminal should close, not traceback.
        return 130
    return 0


class SettingsWindow:
    def __init__(self, Gtk, config):
        self.Gtk = Gtk
        self.config = config
        self.choices = engine.voice_choices(config)

        window = Gtk.Window(title="Steve's Talking Clock")
        window.set_border_width(10)
        window.set_default_size(520, -1)
        window.connect("destroy", Gtk.main_quit)
        self.window = window

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        window.add(outer)

        columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        outer.pack_start(columns, True, True, 0)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        columns.pack_start(left, True, True, 0)

        # -- the six voice lists -----------------------------------------
        self.combos = {}
        for slot in SLOTS:
            label = Gtk.Label(label=SLOT_LABELS[slot] + ":", xalign=0)
            combo = Gtk.ComboBoxText()
            for entry in self.choices:
                combo.append_text(entry)
            current = config.slot_voice(slot)
            combo.set_active(self.choices.index(current) if current in self.choices else 0)
            combo.get_accessible().set_name(SLOT_LABELS[slot])
            label.set_mnemonic_widget(combo)
            combo.connect("changed", self.on_changed)
            left.pack_start(label, False, False, 0)
            left.pack_start(combo, False, False, 0)
            self.combos[slot] = combo

        # -- gap delay ---------------------------------------------------
        left.pack_start(Gtk.Separator(), False, False, 4)
        self.gap_label = Gtk.Label(xalign=0)
        left.pack_start(self.gap_label, False, False, 0)
        self.gap = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 0.20, 0.005)
        self.gap.set_digits(3)
        self.gap.set_value(config.gap_ms / 1000.0)
        self.gap.set_hexpand(True)
        self.gap.get_accessible().set_name("Gap delay, silence inserted between each spoken word")
        self.gap.connect("value-changed", self.on_gap)
        left.pack_start(self.gap, False, False, 0)
        self.update_gap_label(self.gap.get_value())

        # -- alarm time --------------------------------------------------
        alarm_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        left.pack_start(alarm_box, False, False, 4)
        alarm_label = Gtk.Label(label="Alarm time:", xalign=0)
        self.alarm_entry = Gtk.Entry(text=config.get("alarm", "time"))
        self.alarm_entry.set_width_chars(10)
        self.alarm_entry.get_accessible().set_name("Alarm time, hh:mm am or hh:mm")
        self.alarm_entry.connect("changed", self.on_changed)
        alarm_box.pack_start(alarm_label, False, False, 0)
        alarm_box.pack_start(self.alarm_entry, False, False, 0)

        # -- 24 hour mode ------------------------------------------------
        self.hour24 = Gtk.CheckButton(label="24 Hour Mode")
        self.hour24.set_active(config.hour24)
        self.hour24.connect("toggled", self.on_hour24)
        left.pack_start(self.hour24, False, False, 0)

        # -- clock controls ----------------------------------------------
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        columns.pack_start(right, False, False, 0)
        buttons = (
            ("Say Time", "Speaks the time with the Main Voice", self.on_say),
            ("Initialise", "Rescans the voices folder and checks the selected packs", self.on_initialise),
            ("Open Voices Folder", "Opens the folder where voice packs are stored", self.on_open_folder),
            ("Save Settings", "Settings save automatically; this confirms it", self.on_save),
            ("Audio…", "Choose the sound device used for announcements", self.on_audio),
            ("Hotkeys…", "Keyboard shortcuts", self.on_hotkeys),
            ("Quiet Mode…", "Suppress automatic announcements in a time window", self.on_quiet),
            ("Minimise", "Hide the window; the clock keeps running", self.on_minimise),
        )
        for label, hint, handler in buttons:
            button = Gtk.Button(label=label)
            button.set_tooltip_text(hint)
            button.get_accessible().set_name(label.rstrip("…"))
            button.get_accessible().set_description(hint)
            button.connect("clicked", handler)
            right.pack_start(button, False, False, 0)

        self.status = Gtk.Label(label=self.describe(), xalign=0)
        self.status.set_line_wrap(True)
        outer.pack_start(self.status, False, False, 0)

        window.show_all()
        self.combos["main"].grab_focus()

    # -- helpers ---------------------------------------------------------

    def selected(self, combo):
        index = combo.get_active()
        if index < 0:
            return tts.NONE
        return combo.get_model()[index][0]

    def update_gap_label(self, seconds):
        self.gap_label.set_text("Gap Delay: %.3fs" % seconds)

    def describe(self):
        main = self.selected(self.combos["main"]) if self.combos else ""
        pack = engine.resolve_pack(self.config, main)
        if engine.voice_kind(main) == "none":
            return "Main Voice is None - the clock stays silent."
        if engine.voice_kind(main) == "tts":
            return "%s - speech synthesis via %s." % (main, tts.engine_name() or "no engine found")
        if pack is None:
            return "Main Voice %r is not installed." % main
        return "%s - method %d (%s), %d files." % (
            pack.name,
            pack.method(),
            packs.METHOD_NAMES.get(pack.method(), "unknown"),
            len(pack),
        )

    def set_status(self, text):
        self.status.set_text(text)

    def apply(self):
        config = self.config
        for slot, combo in self.combos.items():
            config.set_slot_voice(slot, self.selected(combo))
        config.set("general", "gap_ms", int(round(self.gap.get_value() * 1000)))
        config.set_bool("general", "hour24", self.hour24.get_active())
        config.set("alarm", "time", self.alarm_entry.get_text().strip() or "08:00 am")

    def persist(self):
        self.apply()
        self.config.save()

    # -- signals ---------------------------------------------------------

    def on_changed(self, _widget=None):
        self.persist()
        self.set_status(self.describe())

    def on_gap(self, scale):
        self.update_gap_label(scale.get_value())
        self.config.set("general", "gap_ms", int(round(scale.get_value() * 1000)))
        self.config.save()

    def on_hour24(self, _widget):
        # Same as the Mac: rewrite the alarm time into the newly chosen format.
        minutes = parse_clock(self.alarm_entry.get_text())
        if minutes is not None:
            self.alarm_entry.set_text(format_clock(minutes, self.hour24.get_active()))
        self.persist()
        self.set_status(self.describe())

    # -- buttons ---------------------------------------------------------

    def on_say(self, _button):
        self.persist()
        try:
            self.set_status(engine.announce(self.config, slot="main"))
        except Exception as error:  # keep the window alive
            self.set_status("Could not play: %s" % error)

    def on_initialise(self, _button):
        Gtk = self.Gtk
        self.persist()
        installed = packs.discover(self.config.voices_dir)
        lines = ["Voices folder: %s" % self.config.voices_dir, "Found %d pack(s)." % len(installed), ""]
        ok = True
        reports = engine.validate_selected(self.config)
        if not reports:
            lines.append("No voice packs are selected.")
        for slots, name, validation in reports:
            if validation is None:
                ok = False
                lines.append("%s (used by %s): MISSING" % (name, ", ".join(slots)))
            elif validation.ok:
                lines.append("%s (used by %s): OK" % (name, ", ".join(slots)))
            else:
                ok = False
                lines.append(
                    "%s (used by %s): missing %s"
                    % (name, ", ".join(slots), ", ".join(item[0] for item in validation.missing))
                )
        self.Gdk_beep()
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            flags=0,
            message_type=Gtk.MessageType.INFO if ok else Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK,
            text="Voices refreshed" if ok else "Voice packs need attention",
        )
        dialog.format_secondary_text("\n".join(lines))
        dialog.run()
        dialog.destroy()
        self.set_status(self.describe())

    def Gdk_beep(self):
        try:
            from gi.repository import Gdk

            Gdk.beep()
        except Exception:
            pass

    def on_open_folder(self, _button):
        folder = self.config.voices_dir
        folder.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(["xdg-open", str(folder)])
        except OSError as error:
            self.set_status("Could not open %s: %s" % (folder, error))

    def on_save(self, _button):
        self.persist()
        self.Gdk_beep()
        self.set_status("Settings saved to %s" % self.config.path)

    def on_audio(self, _button):
        Gtk = self.Gtk
        sinks = audio.list_sinks()
        dialog = Gtk.Dialog(title="Audio Output", transient_for=self.window, flags=0)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Apply", Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(10)
        box.add(Gtk.Label(label="Choose the sound device used for clock announcements.", xalign=0))
        combo = Gtk.ComboBoxText()
        combo.append_text("System Default")
        for sink in sinks:
            combo.append_text(sink)
        current = self.config.device
        combo.set_active(sinks.index(current) + 1 if current in sinks else 0)
        combo.get_accessible().set_name("Sound device")
        box.pack_start(combo, False, False, 0)
        if not sinks:
            box.pack_start(
                Gtk.Label(
                    label="No devices listed. Install pulseaudio-utils or pipewire for a device list.",
                    xalign=0,
                ),
                False,
                False,
                0,
            )
        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            index = combo.get_active()
            self.config.set("general", "device", "" if index <= 0 else sinks[index - 1])
            self.config.save()
            self.set_status("Output device: %s" % (self.config.device or "system default"))
        dialog.destroy()

    def on_hotkeys(self, _button):
        Gtk = self.Gtk
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Hotkey Configuration",
        )
        dialog.format_secondary_text(
            "Global hotkeys need an X11 key grab, which this build does not do.\n"
            "The command line covers each one:\n\n"
            "  Say Time          steves-clock say\n"
            "  24 Hour Mode      steves-clock set general.hour24=true\n"
            "  Initialise        steves-clock initialise\n"
            "  Audio             steves-clock config\n"
            "  Quiet             steves-clock set quiet.enabled=true\n"
            "  Minimise          close this window; the service keeps running\n\n"
            "Bind those to keys in your desktop's keyboard settings."
        )
        dialog.run()
        dialog.destroy()

    def on_quiet(self, _button):
        Gtk = self.Gtk
        config = self.config
        dialog = Gtk.Dialog(title="Quiet Mode Settings", transient_for=self.window, flags=0)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "OK", Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(6)
        box.set_border_width(10)

        enable = Gtk.CheckButton(label="Enable Quiet Mode")
        enable.set_active(config.get_bool("quiet", "enabled"))
        enable.get_accessible().set_description(
            "Suppresses automatic announcements during the quiet window."
        )
        stop_entry = Gtk.Entry(text=config.get("quiet", "stop"))
        stop_entry.get_accessible().set_name("Stop speaking after")
        start_entry = Gtk.Entry(text=config.get("quiet", "start"))
        start_entry.get_accessible().set_name("Start speaking from")

        box.pack_start(enable, False, False, 0)
        box.pack_start(Gtk.Label(label="Stop speaking after:", xalign=0), False, False, 0)
        box.pack_start(stop_entry, False, False, 0)
        box.pack_start(Gtk.Label(label="Start speaking from:", xalign=0), False, False, 0)
        box.pack_start(start_entry, False, False, 0)

        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            config.set_bool("quiet", "enabled", enable.get_active())
            config.set("quiet", "stop", stop_entry.get_text().strip() or "11:00 pm")
            config.set("quiet", "start", start_entry.get_text().strip() or "08:00 am")
            config.save()
            self.set_status(
                "Quiet mode %s" % ("on" if enable.get_active() else "off")
            )
        dialog.destroy()

    def on_minimise(self, _button):
        self.window.iconify()


if __name__ == "__main__":
    sys.exit(main())
