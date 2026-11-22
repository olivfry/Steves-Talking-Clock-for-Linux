#!/usr/bin/env bash
#
# Steve's Talking Clock - Linux installer.
#
# The command line is always installed; this lets you add the terminal menu,
# the GTK window, or both, and sets up a systemd user service so the clock runs
# in the background. Safe to run more than once: everything it writes is
# replaced, not appended.
#
set -euo pipefail

SRC=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APP=steves-clock

# ---------------------------------------------------------------- defaults --

if [ "$(id -u)" -eq 0 ]; then
    PREFIX=/usr/local
    SCOPE=system
else
    PREFIX=${HOME}/.local
    SCOPE=user
fi

WITH_GUI=0
MODE_SET=0
DO_SERVICE=1
DO_UNINSTALL=0
DO_PURGE=0
ASSUME_YES=0

CONFIG_HOME=${XDG_CONFIG_HOME:-${HOME}/.config}
DATA_HOME=${XDG_DATA_HOME:-${HOME}/.local/share}

# ------------------------------------------------------------------ output --

if [ -t 1 ]; then
    BOLD=$(printf '\033[1m'); OFF=$(printf '\033[0m')
else
    BOLD=""; OFF=""
fi

say()  { printf '%s\n' "$*"; }
step() { printf '\n%s==> %s%s\n' "$BOLD" "$*" "$OFF"; }
warn() { printf '%sWarning:%s %s\n' "$BOLD" "$OFF" "$*" >&2; }
die()  { printf '%sError:%s %s\n' "$BOLD" "$OFF" "$*" >&2; exit 1; }

usage() {
    cat <<EOF
${BOLD}Usage:${OFF} ./install.sh [options]

The command line is always installed - it is also the menu, since running
`steves-clock` at a terminal opens an interactive one. The only choice is
whether to add the GTK window on top:

  --cli             command line only (default when there is no desktop)
  --gui             also install the GTK window
  --all             same as --gui

  --prefix DIR      install under DIR (default: ~/.local, or /usr/local as root,
                    which installs for every user on the machine)
  --no-service      do not set up the systemd service
  --uninstall       remove installed program files and services
  --purge           with --uninstall, also delete settings and voice packs
  --yes             accept defaults without asking
  -h, --help        show this message
EOF
}

# ------------------------------------------------------------------ parsing --

while [ $# -gt 0 ]; do
    case "$1" in
        --cli)        WITH_GUI=0; MODE_SET=1 ;;
        --tui)        WITH_GUI=0; MODE_SET=1 ;;   # the menu is part of the CLI
        --gui)        WITH_GUI=1; MODE_SET=1 ;;
        --all|--both) WITH_GUI=1; MODE_SET=1 ;;
        --prefix)     [ $# -ge 2 ] || die "--prefix needs a directory"; PREFIX=$2; shift ;;
        --prefix=*)   PREFIX=${1#*=} ;;
        --no-service) DO_SERVICE=0 ;;
        --uninstall)  DO_UNINSTALL=1 ;;
        --purge)      DO_PURGE=1 ;;
        --yes|-y)     ASSUME_YES=1 ;;
        -h|--help)    usage; exit 0 ;;
        *)            die "unknown option: $1 (try --help)" ;;
    esac
    shift
done

case "$PREFIX" in
    /*) ;;
    *) die "--prefix must be an absolute path" ;;
esac

BIN="${PREFIX}/bin"
LIB="${PREFIX}/lib/${APP}"
UNIT="${CONFIG_HOME}/systemd/user/${APP}.service"
DESKTOP="${DATA_HOME}/applications/${APP}.desktop"

# Machine-wide locations, used only for a root install. Both are overridable
# so a package build can stage them somewhere else.
SYS_UNIT=${SYS_UNIT:-/etc/systemd/user/${APP}.service}
AUTOSTART_DIR=${AUTOSTART_DIR:-/etc/xdg/autostart}

# --------------------------------------------------------------- uninstall --

if [ "$DO_UNINSTALL" -eq 1 ]; then
    step "Removing Steve's Talking Clock"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user disable --now "${APP}.service" >/dev/null 2>&1 || true
    fi
    rm -f "$UNIT"
    # Written only by a root install; absent (and unwritable) otherwise.
    rm -f "$SYS_UNIT" 2>/dev/null || true
    rm -f "${AUTOSTART_DIR}/${APP}.desktop" 2>/dev/null || true
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user daemon-reload >/dev/null 2>&1 || true
    fi
    rm -f "$BIN/${APP}" "$BIN/${APP}-gui" "$BIN/${APP}-tui" "$DESKTOP"
    rm -rf "$LIB"
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$DATA_HOME/applications" >/dev/null 2>&1 || true
    fi
    say "Program files removed."

    if [ "$DO_PURGE" -eq 1 ]; then
        rm -rf "${CONFIG_HOME}/${APP}" "${DATA_HOME}/${APP}"
        say "Settings and voice packs deleted."
    else
        say "Kept your settings in ${CONFIG_HOME}/${APP}"
        say "Kept your voice packs in ${DATA_HOME}/${APP}/voices"
        say "Run ./install.sh --uninstall --purge to delete those too."
    fi
    exit 0
fi

# ------------------------------------------------------------ sanity checks --

step "Checking the source tree"
[ -f "${SRC}/stevesclock/__init__.py" ] || die "run this from the linux/ folder of the checkout"
[ -f "${SRC}/steves-clock" ] || die "missing launcher ${SRC}/steves-clock"
[ -f "${SRC}/steves-clock-tui" ] || die "missing launcher ${SRC}/steves-clock-tui"
[ -f "${SRC}/steves-clock-gui" ] || die "missing launcher ${SRC}/steves-clock-gui"
say "Found the source in ${SRC}"

step "Checking Python"
PY=python3
command -v "$PY" >/dev/null 2>&1 || die "python3 is not installed - sudo apt install python3"
"$PY" - <<'PY' || die "Python 3.8 or newer is required"
import sys
raise SystemExit(0 if sys.version_info >= (3, 8) else 1)
PY
say "Using $("$PY" -V 2>&1)"

step "Checking audio and speech"
PLAYER=""
for candidate in paplay pw-play aplay ffplay; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PLAYER=$candidate
        break
    fi
done
if [ -n "$PLAYER" ]; then
    say "Player: ${PLAYER}"
else
    warn "no audio player found - install one of:"
    warn "  sudo apt install pulseaudio-utils     (recommended on Mint)"
    warn "  sudo apt install pipewire-bin   or   sudo apt install alsa-utils"
fi
if command -v spd-say >/dev/null 2>&1 || command -v espeak-ng >/dev/null 2>&1; then
    say "Speech fallback: available"
else
    warn "no speech engine - voice packs still work (speech-dispatcher adds a fallback)"
fi

has_gtk() {
    "$PY" -c 'import gi; gi.require_version("Gtk", "3.0")' >/dev/null 2>&1
}

if [ "$MODE_SET" -eq 0 ]; then
    if has_gtk; then WITH_GUI=1; else WITH_GUI=0; fi
fi

# ------------------------------------------------------------------- prompt --

if [ "$ASSUME_YES" -eq 0 ] && [ -t 0 ] && [ "$MODE_SET" -eq 0 ]; then
    step "The command line is always installed. Add the window too?"
    say "  1) Command line only - run 'steves-clock' for the menu, or use its commands"
    say "  2) Command line and the GTK window"
    default=2
    has_gtk || default=1
    printf 'Choice [%s]: ' "$default"
    read -r answer || answer=""
    case "${answer:-$default}" in
        1) WITH_GUI=0 ;;
        2) WITH_GUI=1 ;;
        *) die "unrecognised choice: $answer" ;;
    esac
fi

if [ "$WITH_GUI" -eq 1 ] && ! has_gtk; then
    warn "GTK 3 for Python is missing, so the window cannot start."
    warn "  sudo apt install python3-gi gir1.2-gtk-3.0"
    warn "Installing the command line only."
    WITH_GUI=0
fi

# ------------------------------------------------------------------ install --

step "Installing to ${PREFIX}"
mkdir -p "$BIN" "$LIB"
rm -rf "${LIB}/stevesclock"
cp -R "${SRC}/stevesclock" "${LIB}/stevesclock"
find "$LIB" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

install -m 0755 "${SRC}/steves-clock" "${BIN}/${APP}"
say "Installed ${BIN}/${APP}"

# The menu is part of the CLI; this alias is kept for older habits.
install -m 0755 "${SRC}/steves-clock-tui" "${BIN}/${APP}-tui"

if [ "$WITH_GUI" -eq 1 ]; then
    install -m 0755 "${SRC}/steves-clock-gui" "${BIN}/${APP}-gui"
    say "Installed ${BIN}/${APP}-gui"
    mkdir -p "${DATA_HOME}/applications"
    cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Steve's Talking Clock
Comment=Announce the time on the quarter hour
Exec=${BIN}/${APP}-gui
Icon=preferences-system-time
Terminal=false
Categories=Utility;Clock;
Keywords=clock;time;talking;accessibility;
EOF
    say "Added a menu entry: ${DESKTOP}"
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "${DATA_HOME}/applications" >/dev/null 2>&1 || true
    fi
else
    rm -f "${BIN}/${APP}-gui"
fi

mkdir -p "${DATA_HOME}/${APP}/voices"

# ----------------------------------------------------------- bundled pack --

# The same Default pack the Mac build ships. Only copied when it is not
# already there, so a pack you have edited or replaced is never clobbered.
step "Installing the bundled voice pack"
found=0
for packdir in "${SRC}"/voices/*/ "${SRC}/../Default/"; do
    [ -d "$packdir" ] || continue
    found=1
    packname=$(basename "$packdir")
    if [ -d "${DATA_HOME}/${APP}/voices/${packname}" ]; then
        say "Keeping your existing ${packname} pack"
    else
        cp -R "$packdir" "${DATA_HOME}/${APP}/voices/${packname}"
        say "Installed ${packname}"
    fi
done
if [ "$found" -eq 0 ]; then
    say "No bundled pack found - add one with: ${APP} install-voice <folder>"
fi

# ------------------------------------------------------------------ service --

if [ "$DO_SERVICE" -eq 1 ]; then
    step "Setting up the background service"
    if [ "$SCOPE" = "system" ]; then
        # Machine-wide. One root daemon cannot reach another user's sound
        # server, and an announcement belongs to the session that plays it, so
        # "for every user" means one daemon per session: a shared unit any
        # account can enable, plus autostart so each desktop login starts it.
        mkdir -p "$(dirname "$SYS_UNIT")" "$AUTOSTART_DIR"
        cat > "$SYS_UNIT" <<EOF
[Unit]
Description=Steve's Talking Clock
After=sound.target

[Service]
Type=simple
ExecStart=${BIN}/${APP} daemon
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
        say "Shared unit for every user: ${SYS_UNIT}"
        cat > "${AUTOSTART_DIR}/${APP}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Steve's Talking Clock
Comment=Announce the time on the quarter hour
Exec=${BIN}/${APP} daemon
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOF
        say "Starts with every desktop login: ${AUTOSTART_DIR}/${APP}.desktop"

        # Root's own session, when this shell actually has one.
        if command -v systemctl >/dev/null 2>&1 && systemctl --user daemon-reload >/dev/null 2>&1; then
            if systemctl --user enable "${APP}.service" >/dev/null 2>&1 \
                && systemctl --user restart "${APP}.service" >/dev/null 2>&1; then
                say "Started ${APP}.service for this account too"
            else
                warn "could not start the service for this account."
                warn "Try: systemctl --user enable --now ${APP}.service"
            fi
        else
            warn "this account has no systemd user instance, so nothing started here."
            warn "That is normal for root over ssh or a console login without a desktop."
            warn "Each user with a session runs:"
            warn "  systemctl --user enable --now ${APP}.service"
            warn "For a headless account, first:  loginctl enable-linger \$USER"
        fi
        say "Logs: journalctl --user -u ${APP} -f"
    else
        mkdir -p "$(dirname "$UNIT")"
        cat > "$UNIT" <<EOF
[Unit]
Description=Steve's Talking Clock
After=sound.target

[Service]
Type=simple
ExecStart=${BIN}/${APP} daemon
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
        if command -v systemctl >/dev/null 2>&1 && systemctl --user daemon-reload >/dev/null 2>&1; then
            # restart rather than start: an upgrade must not leave the old
            # code running in memory.
            if systemctl --user enable "${APP}.service" >/dev/null 2>&1 \
                && systemctl --user restart "${APP}.service" >/dev/null 2>&1; then
                say "Started ${APP}.service (it will start with each login)"
            else
                warn "could not start the service automatically."
                warn "Try: systemctl --user enable --now ${APP}.service"
            fi
        else
            warn "systemd user services are unavailable here."
            warn "The unit is written to ${UNIT}; start it manually with:"
            warn "  systemctl --user daemon-reload && systemctl --user enable --now ${APP}.service"
        fi
        say "Logs: journalctl --user -u ${APP} -f"
    fi
fi

# ------------------------------------------------------------------- finish --

step "Done"
say "Settings:   ${CONFIG_HOME}/${APP}/config.conf"
say "Voice packs: ${DATA_HOME}/${APP}/voices"
say ""
say "Next steps:"
say "  ${APP}                   # menu (or status when piped)"
say "  ${APP} say               # speak the time now"
say "  ${APP} status            # every slot, gap, alarm, quiet"
if [ "$WITH_GUI" -eq 1 ]; then
    say "  ${APP}-gui               # open the window"
fi
say "  ${APP} list              # the voice list"
say "  ${APP} install-voice <folder>   # add another voice pack"
say "  ${APP} initialise        # rescan and check the selected packs"
say ""
if [ "$found" -eq 1 ]; then
    say "The bundled Default pack is installed, so the clock speaks out of the box."
    say "Pick System Voice instead to use speech synthesis."
else
    say "No voice pack found. Add one with: ${APP} install-voice <folder>"
fi
