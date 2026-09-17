#!/usr/bin/env bash
# Per-user install: no root needed.
# Usage: linux/install.sh [--no-autostart] [--no-start]
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}"
LIB="$PREFIX/vr-autolauncher"
BIN="$HOME/.local/bin/vr-autolauncher"
APPS="$PREFIX/applications"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
ICON="$PREFIX/icons/hicolor/scalable/apps/vr-autolauncher.svg"

autostart=1
start=1
for arg in "$@"; do
    case "$arg" in
        --no-autostart) autostart=0 ;;
        --no-start) start=0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

# Dependencies come from the distro, not pip.
missing=()
python3 -c 'import gi; gi.require_version("Gtk", "3.0")' 2>/dev/null || missing+=(gobject)
python3 -c '
import gi
for ns in ("AyatanaAppIndicator3", "AppIndicator3"):
    try:
        gi.require_version(ns, "0.1"); break
    except ValueError: pass
else: raise SystemExit(1)' 2>/dev/null || missing+=(appindicator)
python3 -c 'import PIL' 2>/dev/null || missing+=(pillow)
if [ ${#missing[@]} -gt 0 ]; then
    echo "Missing: ${missing[*]}. Install them first, e.g.:"
    echo "  Fedora:        sudo dnf install python3-gobject libappindicator-gtk3 python3-pillow"
    echo "  Debian/Ubuntu: sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1 python3-pil"
    echo "  Arch:          sudo pacman -S python-gobject libayatana-appindicator python-pillow"
    exit 1
fi

# Stop a running copy so the new code is picked up.
pid_file="${XDG_RUNTIME_DIR:-/tmp}/vr-autolauncher/instance.lock"
if [ -s "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    kill "$(cat "$pid_file")" && sleep 2
fi

rm -rf "$LIB"
mkdir -p "$LIB" "$(dirname "$BIN")" "$APPS" "$(dirname "$ICON")"
cp -r "$SRC/vr_autolauncher" "$LIB/"
find "$LIB" -name __pycache__ -type d -exec rm -rf {} +

cat > "$BIN" <<SH
#!/bin/sh
PYTHONPATH="$LIB\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m vr_autolauncher "\$@"
SH
chmod +x "$BIN"

install -m 644 "$SRC/linux/vr-autolauncher.svg" "$ICON"
sed "s|@BIN@|$BIN|" "$SRC/linux/vr-autolauncher.desktop" > "$APPS/vr-autolauncher.desktop"
if [ $autostart = 1 ]; then
    mkdir -p "$AUTOSTART"
    # Autostart goes straight to the tray; the app-menu entry opens the settings window.
    sed -e "s|^Exec=.*|Exec=$BIN --background|" -e '$a NoDisplay=true' \
        "$APPS/vr-autolauncher.desktop" > "$AUTOSTART/vr-autolauncher.desktop"
fi
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q "$PREFIX/icons/hicolor" 2>/dev/null || true

echo "Installed to $LIB"
echo "  command:   $BIN (--status shows what it detects)"
echo "  config:    ${XDG_CONFIG_HOME:-$HOME/.config}/vr-autolauncher/config.json (created on first run)"
if [ $autostart = 1 ]; then echo "  autostart: $AUTOSTART/vr-autolauncher.desktop"; fi
if [ "${XDG_CURRENT_DESKTOP:-}" = "GNOME" ] && ! gnome-extensions list --enabled 2>/dev/null | grep -q appindicator; then
    echo "NOTE: GNOME needs the 'AppIndicator and KStatusNotifierItem Support' extension to show the tray icon."
fi

if [ $start = 1 ]; then
    setsid "$BIN" --background >/dev/null 2>&1 < /dev/null &
    echo "Started."
fi
