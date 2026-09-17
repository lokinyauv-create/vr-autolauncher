#!/usr/bin/env bash
# Removes the program; keeps your config (~/.config/vr-autolauncher) and logs.
set -eu
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}"
pid_file="${XDG_RUNTIME_DIR:-/tmp}/vr-autolauncher/instance.lock"
if [ -s "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    kill "$(cat "$pid_file")"
fi
rm -rf "$PREFIX/vr-autolauncher"
rm -f "$HOME/.local/bin/vr-autolauncher" \
      "$PREFIX/applications/vr-autolauncher.desktop" \
      "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/vr-autolauncher.desktop" \
      "$PREFIX/icons/hicolor/scalable/apps/vr-autolauncher.svg"
echo "Uninstalled (config and logs kept)."
