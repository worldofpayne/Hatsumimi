#!/usr/bin/env bash
# Install (or update) Hatsumimi on Arch / Omarchy.
#
#   ./packaging/linux/install.sh        (run from anywhere inside the project)
#
# Installs the app with uv as the `hatsumimi` command, plus a launcher entry
# and icon, so it shows up in your app launcher like any other program.
# It uses Arch's own libmpv, ffmpeg and Qt Wayland support.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
STEP="starting"

pink=$'\e[38;5;211m'; red=$'\e[31m'; green=$'\e[32m'; bold=$'\e[1m'; off=$'\e[0m'
step() { STEP="$1"; echo; echo "${pink}${bold}:: $1${off}"; }
fail() {
    echo
    echo "${red}${bold}Hatsumimi was NOT installed.${off} The step that failed: ${bold}${STEP}${off}"
    echo "Scroll up for the error message, or rerun with:  $0 2>&1 | tee ~/hatsumimi-install.log"
}
trap fail ERR

# A command only counts if it actually runs. On Omarchy, tools like uv can be
# mise "shims": the command exists but the real program isn't installed yet.
works() { "$@" >/dev/null 2>&1; }

# 1. System packages (only the missing ones; asks for your password).
step "Checking system packages"
need=()
works mpv --version      || need+=(mpv)            # provides libmpv
works ffmpeg -version    || need+=(ffmpeg)         # re-timing subtitles
works mkvmerge --version || need+=(mkvtoolnix-cli) # embedding subtitles
works uv --version       || need+=(uv)
if ((${#need[@]})); then
    echo "Installing: ${need[*]}"
    sudo pacman -S --needed "${need[@]}"
else
    echo "All present (mpv, ffmpeg, mkvmerge, uv)."
fi

# If uv was a mise shim that couldn't run, prefer the real one pacman installed.
UV="uv"
if ! works "$UV" --version && [ -x /usr/bin/uv ]; then UV=/usr/bin/uv; fi
works "$UV" --version || { STEP="finding a working uv"; false; }
echo "Using $("$UV" --version)"

# 2. The app itself (into its own isolated environment).
step "Installing Hatsumimi (downloads Python 3.12 and the app's libraries the first time)"
"$UV" tool install --force --python 3.12 "$ROOT"
BIN="$("$UV" tool dir --bin)"
BIN="$(cd "$BIN" && pwd)"   # tidy paths like ~/.local/share/../bin

# 3. Launcher entry + icon.
step "Adding the launcher entry and icon"
install -Dm644 "$ROOT/src/hatsumimi/app/assets/logo.svg" "$DATA/icons/hicolor/scalable/apps/hatsumimi.svg"
install -Dm644 "$ROOT/src/hatsumimi/app/assets/hatsumimi.png" "$DATA/icons/hicolor/512x512/apps/hatsumimi.png"
mkdir -p "$DATA/applications"
sed "s|@BIN@|$BIN|g" "$ROOT/packaging/linux/hatsumimi.desktop" > "$DATA/applications/hatsumimi.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" || true
command -v gtk-update-icon-cache  >/dev/null && gtk-update-icon-cache -q "$DATA/icons/hicolor" || true
# Omarchy's launcher (Walker) keeps its app list in memory: refresh it.
if command -v omarchy-restart-walker >/dev/null; then omarchy-restart-walker >/dev/null 2>&1 || true; fi

# 4. Check it really worked.
step "Checking the install"
STEP="checking the install"
[ -x "$BIN/hatsumimi" ] || { echo "${red}Missing: $BIN/hatsumimi${off}"; false; }
[ -f "$DATA/applications/hatsumimi.desktop" ] || { echo "${red}Missing launcher entry${off}"; false; }
echo "Command:        $BIN/hatsumimi"
echo "Launcher entry: $DATA/applications/hatsumimi.desktop"
trap - ERR

echo
echo "${green}${bold}Hatsumimi is installed.${off} Start it from your launcher, or run: hatsumimi"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "Note: $BIN isn't on your PATH yet, so the 'hatsumimi' command won't work in a terminal."
       echo "      Fix it with:  $UV tool update-shell   (then open a new terminal)" ;;
esac
echo "First run: Settings -> paste your Jimaku API key, then 'Download dictionary'."
echo "Optional standalone-mpv panel: install-mpv-script"
