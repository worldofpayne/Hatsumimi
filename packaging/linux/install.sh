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

# 1. System packages (only the missing ones; asks for your password).
need=()
command -v uv       >/dev/null || need+=(uv)
command -v mpv      >/dev/null || need+=(mpv)            # provides libmpv
command -v ffmpeg   >/dev/null || need+=(ffmpeg)         # re-timing subtitles
command -v mkvmerge >/dev/null || need+=(mkvtoolnix-cli) # embedding subtitles
if ((${#need[@]})); then
    echo ":: Installing ${need[*]}"
    sudo pacman -S --needed "${need[@]}"
fi

# 2. The app itself (into its own isolated environment).
echo ":: Installing Hatsumimi"
uv tool install --force --python 3.12 "$ROOT"
BIN="$(uv tool dir --bin)"

# 3. Launcher entry + icon.
install -Dm644 "$ROOT/src/hatsumimi/app/assets/logo.svg" "$DATA/icons/hicolor/scalable/apps/hatsumimi.svg"
install -Dm644 "$ROOT/src/hatsumimi/app/assets/hatsumimi.png" "$DATA/icons/hicolor/512x512/apps/hatsumimi.png"
mkdir -p "$DATA/applications"
sed "s|@BIN@|$BIN|g" "$ROOT/packaging/linux/hatsumimi.desktop" > "$DATA/applications/hatsumimi.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" || true
command -v gtk-update-icon-cache  >/dev/null && gtk-update-icon-cache -q "$DATA/icons/hicolor" || true

echo
echo "Done. Start Hatsumimi from your launcher, or run: hatsumimi"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "(Add $BIN to your PATH to run it from a terminal.)" ;; esac
echo "First run: Settings -> paste your Jimaku API key, then 'Download dictionary'."
echo "Optional standalone-mpv panel: install-mpv-script"
