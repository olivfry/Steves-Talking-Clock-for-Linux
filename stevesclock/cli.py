"""Command-line front end.

Command names follow the Mac window's buttons: ``say`` is Say Time,
``initialise`` is Initialise, ``list`` is the voice picker, and the six slots
are the six voice lists.
"""

from __future__ import annotations

import argparse
import datetime
import fcntl
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from . import __version__, audio, engine, packs, schedule, tts
from .config import SLOT_LABELS, SLOTS, Config, format_clock, parse_clock

VERSION = __version__


def _log(message):
    print(message, file=sys.stderr)


def _at(text):
    """Parse --at into a datetime today."""
    minutes = parse_clock(text)
    if minutes is None:
        raise SystemExit("could not understand the time %r - use 8:00 am or 08:00" % text)
    return datetime.datetime.combine(datetime.date.today(), datetime.time(minutes // 60, minutes % 60))


def _slot_note(config, name):
    kind = engine.voice_kind(name)
    if kind == "none":
        return "silent"
    if kind == "tts":
        return "speech synthesis" + ("" if tts.engine_name() else " (no engine installed)")
    pack = engine.resolve_pack(config, name)
    if pack is None:
        return "not installed"
    return "method %d - %s, %d files" % (
        pack.method(),
        packs.METHOD_NAMES.get(pack.method(), ""),
        len(pack),
    )


# -- commands -------------------------------------------------------------


def cmd_status(config, args):
    print("Steve's Talking Clock - Linux %s" % VERSION)
    print("Config:        %s" % config.path)
    print("Voices folder: %s" % config.voices_dir)
    print("Packs:         %d installed" % len(packs.discover(config.voices_dir)))
    print("")
    for slot in SLOTS:
        stored = config.slot_voice(slot)
        note = _slot_note(config, stored)
        if slot == "alarm" and stored == tts.NONE:
            note = "falls back to the Main Voice"
        print("%-32s %-24s %s" % (SLOT_LABELS[slot] + ":", stored, note))
    print("")
    print("Gap Delay:     %.3fs" % (config.gap_ms / 1000.0))
    print("Alarm time:    %s" % config.get("alarm", "time"))
    print("24 Hour Mode:  %s" % ("on" if config.hour24 else "off"))
    quiet = config.quiet_window()
    if quiet:
        print(
            "Quiet mode:    on, after %s until %s"
            % (format_clock(quiet[0], config.hour24), format_clock(quiet[1], config.hour24))
        )
    else:
        print(
            "Quiet mode:    off (%s to %s)"
            % (
                format_clock(parse_clock(config.get("quiet", "stop"), 23 * 60), config.hour24),
                format_clock(parse_clock(config.get("quiet", "start"), 8 * 60), config.hour24),
            )
        )
    print("Output device: %s" % (config.device or "system default"))
    command = audio.player_command(config.player, config.device)
    print("Playback:      %s" % (" ".join(command) if command else "no player found"))
    return 0


def cmd_say(config, args):
    when = _at(args.at) if args.at else datetime.datetime.now()
    if args.hour24:
        config.set_bool("general", "hour24", True)
    if args.words:
        name = args.voice or engine.voice_for(config, args.slot)
        pack = engine.resolve_pack(config, name)
        if pack is None:
            if engine.voice_kind(name) == "none":
                print("(silent - %s is set to None)" % SLOT_LABELS[args.slot])
            else:
                print(engine.time_text(when, config.hour24))
            return 0
        print(" ".join(packs.announcement_files(pack, when, config.hour24)))
        return 0
    if args.to_file:
        print(
            engine.announce(
                config, when, slot=args.slot, voice=args.voice, to_file=args.to_file, log=_log
            )
        )
        return 0
    print(engine.announce(config, when, slot=args.slot, voice=args.voice, log=_log))
    return 0


def _lock_path():
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    base = runtime if runtime and os.path.isdir(runtime) else tempfile.gettempdir()
    return os.path.join(base, "steves-clock.lock")


# raw.githubusercontent.com is cached for minutes, so a release published a
# moment ago still reads as the previous version there. The API is current, so
# it is asked first and the raw file is only a fallback.
UPDATE_VERSION_API = os.environ.get(
    "STEVES_CLOCK_UPDATE_VERSION_API",
    "https://api.github.com/repos/olivfry/Steves-Talking-Clock-for-Linux/contents/stevesclock/__init__.py",
)
UPDATE_VERSION_URL = os.environ.get(
    "STEVES_CLOCK_UPDATE_VERSION_URL",
    "https://raw.githubusercontent.com/olivfry/Steves-Talking-Clock-for-Linux/main/stevesclock/__init__.py",
)
UPDATE_ARCHIVE_URL = os.environ.get(
    "STEVES_CLOCK_UPDATE_ARCHIVE_URL",
    "https://github.com/olivfry/Steves-Talking-Clock-for-Linux/archive/refs/heads/main.tar.gz",
)


def _version_tuple(text):
    parts = []
    for piece in str(text).strip().split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _fetch_text(url, headers=None):
    request = urllib.request.Request(
        url, headers={"User-Agent": "steves-clock/%s" % VERSION, **(headers or {})}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", "replace")


def _version_sources():
    # A test or a mirror can pin the source; otherwise ask the API, then raw.
    # "mirror" is flagged because that copy is cached for minutes.
    if os.environ.get("STEVES_CLOCK_UPDATE_VERSION_URL"):
        return [(UPDATE_VERSION_URL, {}, "pinned")]
    return [
        (UPDATE_VERSION_API, {"Accept": "application/vnd.github.raw"}, "api"),
        (UPDATE_VERSION_URL, {}, "mirror"),
    ]


def _published_version():
    """The __version__ on the published branch, read straight from the repo."""
    problems = []
    for url, headers, label in _version_sources():
        try:
            body = _fetch_text(url, headers)
        except Exception as error:
            problems.append("%s (%s)" % (url, error))
            continue
        match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', body)
        if match:
            return match.group(1).strip(), label
        problems.append("no __version__ at %s" % url)
    raise ValueError("; ".join(problems))


def _install_prefix():
    """The prefix this copy was installed under, or None for a checkout."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "bin" / "steves-clock").exists():
            return parent
    return None


def cmd_update(config, args):
    """Check the repository and, unless --check, install the newer build."""
    print("Installed: %s" % VERSION)
    try:
        latest, source = _published_version()
    except Exception as error:  # offline, blocked, or the file moved
        print("could not check for updates: %s" % error)
        print("The installer is at %s" % UPDATE_ARCHIVE_URL)
        return 1
    note = "" if source != "mirror" else " (mirror, can lag a few minutes)"
    print("Published: %s%s" % (latest, note))

    newer = _version_tuple(latest) > _version_tuple(VERSION)
    force = getattr(args, "force", False)
    if newer:
        print("Update available: %s -> %s" % (VERSION, latest))
    if not newer and not force:
        if latest == VERSION:
            print("Already up to date.")
        else:
            print("This copy is newer than the published one.")
            if source == "mirror":
                # A cached file cannot prove there is nothing newer.
                print("That mirror lags, so a recent release may not show here yet.")
                print("To install whatever is on the branch now: steves-clock update --force")
        return 0
    if getattr(args, "check", False):
        if newer:
            print("Run 'steves-clock update' to install it.")
        else:
            print("Nothing newer is published; --force would reinstall this build.")
        return 0

    prefix = _install_prefix()
    if prefix is None:
        print("This copy runs from a source checkout, so update it there:")
        print("  git pull && ./install.sh")
        return 0

    with tempfile.TemporaryDirectory() as work:
        archive = os.path.join(work, "update.tar.gz")
        print("Downloading %s" % UPDATE_ARCHIVE_URL)
        urllib.request.urlretrieve(UPDATE_ARCHIVE_URL, archive)
        with tarfile.open(archive) as tar:
            try:
                tar.extractall(work, filter="data")
            except TypeError:  # older Python without the filter argument
                tar.extractall(work)
        roots = [
            candidate
            for candidate in Path(work).iterdir()
            if candidate.is_dir() and (candidate / "install.sh").exists()
        ]
        if not roots:
            print("the archive had no install.sh - nothing was changed")
            return 1
        command = ["bash", str(roots[0] / "install.sh"), "--yes", "--prefix", str(prefix)]
        if (prefix / "bin" / ("steves-clock-gui")).exists():
            command.append("--all")
        print("Installing %s into %s%s" % (latest, prefix, " (forced)" if force else ""))
        code = subprocess.call(command)
        if code != 0:
            print("the installer exited %d; the previous version is still installed" % code)
            return code

    print("Updated to %s." % latest)
    print("The installer restarted the service where systemd is available.")
    print("Reopen the window to pick up the new code.")
    return 0


def cmd_daemon(config, args):
    # Autostart and an enabled user unit can both try to start this, and a
    # machine-wide install adds one more way in; a second daemon would speak
    # every announcement twice, so only the first one keeps the lock.
    try:
        # "a+" so a losing daemon does not truncate the winner's pid.
        lock_file = open(_lock_path(), "a+")
    except OSError as error:
        print("warning: no lock file (%s); continuing" % error, file=sys.stderr)
        lock_file = None
    if lock_file is not None:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("a steves-clock daemon is already running", file=sys.stderr)
            lock_file.close()
            return 1
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write("%d\n" % os.getpid())
        lock_file.flush()

    def announce(target, slot):
        return engine.announce(config, target, slot=slot, log=_log)

    try:
        return schedule.run(config, announce, log=_log)
    except KeyboardInterrupt:
        return 0
    finally:
        if lock_file is not None:
            lock_file.close()


def cmd_list(config, args):
    """The voice picker, showing which slots use which entry."""
    usage = {}
    for slot in SLOTS:
        usage.setdefault(config.slot_voice(slot), []).append(slot)
    main_voice = config.slot_voice("main")

    for entry in engine.voice_choices(config):
        mark = "*" if entry == main_voice else " "
        used = ", ".join(usage.get(entry, []))
        note = _slot_note(config, entry)
        print("%s %-28s %-38s %s" % (mark, entry, note, used))
    return 0


def cmd_validate(config, args):
    targets = list(args.pack)
    if not targets:
        selected = [name for _, name, _ in engine.validate_selected(config)]
        targets = [
            str(config.voices_dir / name) for name in selected
        ]
    if not targets:
        installed = packs.discover(config.voices_dir)
        if not installed:
            print("No packs to validate in %s" % config.voices_dir)
            return 1
        targets = [pack.path for pack in installed]

    status = 0
    for path in targets:
        try:
            pack = packs.Pack(path)
        except packs.PackError as error:
            print("FAIL  %s" % error)
            status = 1
            continue
        report = packs.Validation(pack, args.hour24 or config.hour24)
        print(report.report())
        print("")
        if not report.ok:
            status = 1
    return status


def cmd_initialise(config, args):
    """Rescan the voices folder and check every selected pack, like the button."""
    installed = packs.discover(config.voices_dir)
    print("Voices folder: %s" % config.voices_dir)
    print("Found %d pack(s): %s" % (len(installed), ", ".join(pack.name for pack in installed) or "none"))
    print("")

    reports = engine.validate_selected(config)
    if not reports:
        print("No voice packs are selected - every slot is silent or using speech.")
        return 0

    status = 0
    for slots, name, validation in reports:
        print("%s (used by %s)" % (name, ", ".join(slots)))
        if validation is None:
            print("  MISSING - this pack is not in the voices folder")
            status = 1
            continue
        if validation.ok:
            print("  OK - method %d, %d files" % (validation.method, len(validation.pack)))
        else:
            status = 1
            print("  MISSING %d file(s):" % len(validation.missing))
            for filename, hour, minute in validation.missing:
                print("    %-14s needed at %02d:%02d" % (filename, hour, minute))
    return status


def cmd_render(config, args):
    when = _at(args.at) if args.at else datetime.datetime.now()
    if args.hour24:
        config.set_bool("general", "hour24", True)
    engine.announce(config, when, slot=args.slot, to_file=args.output, log=_log)
    print("%s  (%.2fs)" % (args.output, audio.duration(args.output)))
    return 0


def cmd_install_voice(config, args):
    source = os.path.abspath(os.path.expanduser(args.source))
    if not os.path.isdir(source):
        raise SystemExit("not a folder: %s" % source)
    try:
        pack = packs.Pack(source)
    except packs.PackError as error:
        raise SystemExit(str(error))
    destination = config.voices_dir / pack.name
    config.voices_dir.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not args.force:
            raise SystemExit("%s already exists - pass --force to replace it" % destination)
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    config.set_slot_voice("main", pack.name)
    config.save()
    print(
        "Installed %s (method %d, %d files) and set it as the Main Voice."
        % (pack.name, pack.method(), len(pack))
    )
    return 0


def cmd_set(config, args):
    if "." not in args.assignment or "=" not in args.assignment:
        raise SystemExit(
            "use section.key=value, e.g. voices.quarter=Default, general.gap_ms=60, "
            "quiet.stop=11:00 pm"
        )
    key, value = args.assignment.split("=", 1)
    section, name = key.split(".", 1)
    if section not in ("general", "voices", "quiet", "alarm"):
        raise SystemExit("unknown section %r" % section)
    if section == "voices" and name not in SLOTS:
        raise SystemExit("voice slots are: %s" % ", ".join(SLOTS))
    config.set(section, name, value)
    path = config.save()
    print("Saved to %s" % path)
    return 0


def cmd_config(config, args):
    if args.path_only:
        print(config.path)
        return 0
    if config.path.is_file():
        print(config.path.read_text(encoding="utf-8"))
    else:
        print("%s does not exist yet; run any command that saves to create it." % config.path)
    return 0


# -- entry point ----------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        prog="steves-clock",
        description="A talking clock that plays concatenation.txt voice packs.",
    )
    parser.add_argument("--version", action="version", version="steves-clock %s" % VERSION)
    parser.add_argument("--config", help="use a different config file")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    parser.epilog = (
        "With no command, opens the menu when run in a terminal and prints the "
        "status otherwise. Also: steves-clock status|say|initialise|list|daemon|update|set|config"
    )

    status = subparsers.add_parser("status", help="show settings and installed packs")
    status.set_defaults(func=cmd_status)

    say = subparsers.add_parser("say", help="Say Time - announce once")
    say.add_argument("--at", help="time to announce, e.g. 13:05 or 1:05 pm")
    say.add_argument("--slot", choices=SLOTS, default="main", help="which voice list to use")
    say.add_argument("--24", dest="hour24", action="store_true", help="use 24-hour mode")
    say.add_argument("--voice", help="use this voice instead of the slot's")
    say.add_argument("--to-file", help="write a WAV here instead of playing")
    say.add_argument("--words", action="store_true", help="print the file names instead")
    say.set_defaults(func=cmd_say)

    daemon = subparsers.add_parser("daemon", help="run in the foreground, announcing on schedule")
    daemon.set_defaults(func=cmd_daemon)

    update = subparsers.add_parser("update", help="check GitHub for a newer version and install it")
    update.add_argument("--check", action="store_true", help="only report whether one exists")
    update.add_argument("--force", action="store_true", help="reinstall the published build regardless")
    update.set_defaults(func=cmd_update)

    listing = subparsers.add_parser("list", help="the voice list, with the slots using each entry")
    listing.set_defaults(func=cmd_list)
    voices = subparsers.add_parser("voices", help="same as list")
    voices.set_defaults(func=cmd_list)

    validate = subparsers.add_parser("validate", help="check packs against concatenation.txt")
    validate.add_argument("pack", nargs="*", help="pack folders (default: those selected)")
    validate.add_argument("--24", dest="hour24", action="store_true", help="validate for 24-hour mode")
    validate.set_defaults(func=cmd_validate)

    initialise = subparsers.add_parser(
        "initialise", help="rescan the voices folder and check the selected packs"
    )
    initialise.set_defaults(func=cmd_initialise)

    render = subparsers.add_parser("render", help="render an announcement to a WAV")
    render.add_argument("output")
    render.add_argument("--at", help="time to render, e.g. 13:05")
    render.add_argument("--slot", choices=SLOTS, default="main")
    render.add_argument("--24", dest="hour24", action="store_true")
    render.set_defaults(func=cmd_render)

    install_voice = subparsers.add_parser("install-voice", help="copy a pack into the voices folder")
    install_voice.add_argument("source")
    install_voice.add_argument("--force", action="store_true", help="replace an existing pack")
    install_voice.set_defaults(func=cmd_install_voice)

    setter = subparsers.add_parser("set", help="change a setting, e.g. voices.quarter=Default")
    setter.add_argument("assignment")
    setter.set_defaults(func=cmd_set)

    show_config = subparsers.add_parser("config", help="print the config file")
    show_config.add_argument("--path", dest="path_only", action="store_true", help="print only the file name")
    show_config.set_defaults(func=cmd_config)

    return parser


def _interactive():
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    config = Config(args.config)
    if not getattr(args, "func", None):
        # No subcommand. Sitting at a terminal, that means the menu; in a pipe
        # or a script it must not block, so print the status instead.
        if _interactive():
            from .tui import Menu

            return Menu(config).run()
        return cmd_status(config, args)
    try:
        return args.func(config, args)
    except (audio.AudioError, engine.EngineError, tts.TtsError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
