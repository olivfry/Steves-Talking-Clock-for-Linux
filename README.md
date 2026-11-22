# Steve's Talking Clock — Linux

A Linux port of [Steve's Talking Clock](http://clock.steve-audio.net), reading
the same `concatenation.txt` voice packs as the Windows original and the
[macOS port](https://github.com/olivfry/Steves-Talking-Clock-for-the-Mac-).

Python 3.8+, standard library only. No build step. The command line is also the
menu (run `steves-clock` with no arguments); the GTK window is optional.

---

## Install

```bash
git clone https://github.com/olivfry/Steves-Talking-Clock-for-Linux.git
cd Steves-Talking-Clock-for-Linux
./install.sh
```

The command line is always installed — it is the menu too — so the installer
asks one thing:

```
The command line is always installed. Add the window too?
  1) Command line only - run 'steves-clock' for the menu, or use its commands
  2) Command line and the GTK window
```

It then copies the `Default` pack the Mac build ships and enables a systemd
**user** service so the clock runs from login onwards.

| Option | Effect |
| --- | --- |
| `--cli` / `--gui` / `--all` | skip the question |
| `--prefix DIR` | install under `DIR` (default `~/.local`, or `/usr/local` as root) |
| `--no-service` | don't create or start the systemd unit |
| `--uninstall` | remove programme files, launchers, menu entry and unit |
| `--uninstall --purge` | also delete settings and voice packs |
| `--yes` | accept the defaults, no prompt |

Re-running is safe: files are replaced, an existing `Default` pack is left
alone, and the service is restarted so upgraded code actually takes effect.

## The menu

Running `steves-clock` with no arguments at a terminal opens the same controls
the window has; piped or in a script it prints the status instead, so it never
blocks. `steves-clock-tui` is an alias for the same thing:

```
Steve's Talking Clock 0.9.0
  Main Voice List:               Default       method 1 - Simple
  Quarter Hour Voice List:       None          silent
  …
  Gap Delay:                     0.030s
  Alarm time:                    08:00 am
  24 Hour Mode:                  off
  Quiet mode:                    off

Menu:
  1) Main Voice List          2) Quarter Hour Voice List
  3) Half Hour Voice List     4) Three Quarter Hour Voice List
  5) On the Hour Voice List   6) Alarm Voice List
  7) Gap Delay                8) Alarm time
  9) 24 Hour Mode (toggle)    S) Say Time
  I) Initialise               O) Voices folder: path and packs
  P) Add a voice pack         A) Audio output device
  U) Update (check GitHub)    Q) Quiet Mode
  K) Hotkeys                  X) Exit
```

The summary also reports the **background service** state, so closing the menu
with X tells you whether the clock keeps announcing (`active`) or whether
nothing will happen until you start it. `O` prints the folder path and the
packs inside it and only launches a file manager when a display exists, since
`xdg-open` on a headless box achieves nothing; `P` copies a pack in from a path
you type.

It is deliberately **not** curses. A full-screen curses interface repaints
constantly and a screen reader reads the wreckage; this prints one numbered
prompt at a time and re-prints the state after every change, so Orca has
something coherent to read. Voice lists are numbered — type the number, or the
voice name, and blank cancels.

## The window

Laid out like the Mac build, in the same two columns.

**Left** — the six voice lists, the gap delay, the alarm time and 24 Hour Mode:

- Main Voice List
- Quarter Hour Voice List
- Half Hour Voice List
- Three Quarter Hour Voice List
- On the Hour Voice List
- Alarm Voice List
- Gap Delay: `0.030s` — slider, 0.000 to 0.200, silence inserted after each
  spoken word
- Alarm time — `hh:mm am`, or `hh:mm` for 24-hour time
- 24 Hour Mode

**Right** — Say Time, Initialise, Open Voices Folder, Save Settings, Audio…,
Hotkeys…, Quiet Mode…, Minimise.

Settings save as you change them, exactly as `@AppStorage` does on the Mac.
"Save Settings" is kept for parity and beeps.

## Voice rules (identical to the Mac)

- Each slot has its own voice, chosen from one list: `None`, voice packs, and
  speech-synthesis entries.
- Automatic announcements **never** fall back to the main voice. A slot set to
  `None` is silent.
- The alarm **does** fall back to the main voice when its own slot is `None`,
  and it bypasses quiet mode.
- The alarm has no on/off switch, matching the Mac: it fires at its time every
  day, using whatever the alarm slot resolves to.

## Command line

Every button has a command:

| Mac control | Command |
| --- | --- |
| Say Time | `steves-clock say` |
| Initialise | `steves-clock initialise` |
| Open Voices Folder | `xdg-open ~/.local/share/steves-clock/voices` |
| Save Settings | `steves-clock set section.key=value` |
| Audio… | `steves-clock set general.device=<sink>` |
| Hotkeys… | see *Not ported* below |
| Quiet Mode… | `steves-clock set quiet.enabled=true` |
| Check for updates | `steves-clock update` |

```bash
steves-clock status          # every slot, gap, alarm, quiet, device
steves-clock list            # the voice list, with the slots using each entry
steves-clock say             # Say Time, using the Main Voice
steves-clock say --slot quarter --at 09:15
steves-clock say --at 09:05 --words     # print the file names
steves-clock render out.wav --at 21:45 --slot hour
steves-clock initialise      # rescan and check the selected packs
steves-clock validate --24   # check a pack against concatenation.txt
steves-clock install-voice ~/Downloads/SomePack
steves-clock daemon          # foreground, announces on schedule
```

Settings:

```bash
steves-clock set voices.main=Default
steves-clock set voices.quarter=Default
steves-clock set voices.half=None
steves-clock set general.gap_ms=60        # 0.060 s
steves-clock set general.hour24=true
steves-clock set quiet.enabled=true
steves-clock set quiet.stop="11:00 pm"    # stop speaking after
steves-clock set quiet.start="08:00 am"   # start speaking from
steves-clock set alarm.time="07:30 am"
steves-clock set general.device=alsa_output.pci-0000_00_1f.3.analog-stereo
steves-clock config          # print the file
```

## Voices

The installer copies the same `Default` pack the Mac build ships into
`~/.local/share/steves-clock/voices/Default`, and only when that folder is not
already there. Add more with `install-voice`; any folder of `.wav` files
following [`concatenation.txt`](concatenation.txt) works, and the method
(1–10) is detected from the files present.

Windows packs carry `AM.wav`/`PM.wav`; lookups here are case-insensitive, so
they load unchanged.

### System voices

`System Voice` and `System: <name>` speak the same sentence as the Mac,
word for word:

> The time is 3 oh 5 P M
>
> The time is 15 oh 5

`o'clock` on the hour, `oh` before single-digit minutes, `A M`/`P M` spaced so
it is read as two letters. That comes from `timeText(for:use24:)` in the Mac
source. One entry is offered per language rather than per espeak variant;
a variant name such as `System: Tatar+Robosoft7` still works if you set it by
hand. Routing is Speech Dispatcher, then espeak-ng:

```bash
sudo apt install speech-dispatcher      # recommended
# or
sudo apt install espeak-ng
```

## Playback

Words are decoded, mixed to 16-bit at the highest sample rate present, and
concatenated into one temporary WAV — with `gap_ms` of silence after every file
and 0.3 s at the end, the same shape the Mac build renders. Playing files one
`paplay` at a time would insert a much bigger gap at each boundary.

Player detection order: `paplay`, `pw-play`, `aplay`, `ffplay`. Override with
`steves-clock set general.player=pw-play`.

## Background service

The installer writes `~/.config/systemd/user/steves-clock.service`, enables it
and restarts it.

```bash
systemctl --user status steves-clock
journalctl --user -u steves-clock -f
```

To keep running when you are not logged in: `loginctl enable-linger "$USER"`.

## Updating

Nothing phones home and nothing checks on a timer: the clock asks GitHub only
when you do.

```bash
steves-clock update --check    # report installed vs published, change nothing
steves-clock update            # download the current build and reinstall it
```

`update` reads the version from the repository, downloads that branch's
archive, runs its installer with the prefix this copy already uses (adding the
window if you had it) and lets it restart the service. Settings, voice packs and
the config file survive, because the installer only replaces programme files.
Reopen the window afterwards to pick up the new code.

From a source checkout it does not guess: it tells you to `git pull` and
re-run `./install.sh`.

## Layout

```
  install.sh              installer and uninstaller
  steves-clock            CLI launcher
  steves-clock-tui        terminal menu launcher
  steves-clock-gui        GUI launcher
  stevesclock/
    packs.py              method detection, word lists, validation
    audio.py              WAV decoding, concatenation, players, devices
    config.py             XDG paths and the INI config
    engine.py             slot voices, TTS, playback
    tts.py                speech synthesis and voice listing
    schedule.py           boundary maths and the daemon loop
    cli.py                command line front end
    tui.py                terminal menu
    gui.py                GTK 3 window
  systemd/                reference copy of the unit the installer writes
```

## Not ported

- **Global hotkeys.** They need an X11 key grab. Bind `steves-clock say` and
  friends in your desktop's own keyboard settings instead.
- **The launch-at-login prompt.** The installer sets up the service instead;
  updates are `steves-clock update`.
- **Per-device volume routing** beyond naming an output sink.

## Development notes

This port was designed, tested and integrated by Oliver Fry. Freebuff was used
as a coding assistant throughout development: suggesting fixes for specific
technical problems, drafting implementations that were then reviewed and
adapted, and helping work through the Linux interfaces it relies on —
Speech Dispatcher and espeak for synthesis, PulseAudio/PipeWire for playback,
`pactl` for device discovery, systemd user units for the background service and
GTK 3 for the window. All architectural decisions, feature scope, UI design,
testing and accessibility validation were done by the author.

The project is released openly so that anyone can audit the code, understand
what it does, and verify its behaviour — regardless of how it was written.

## Voice pack licensing

Voice packs belong to their creators. Nothing in this repository grants the
right to redistribute one. See [`NOTICE.md`](NOTICE.md).
