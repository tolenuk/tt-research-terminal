#!/usr/bin/env bash
# Adds TT Research Terminal to the desktop menu, with its icon, for the current user.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
assets="$root/src/obb_terminal/ui/assets"
share="${XDG_DATA_HOME:-$HOME/.local/share}"
for size in 16 24 32 48 64 128 256 512; do
    install -Dm644 "$assets/tt-research-$size.png" "$share/icons/hicolor/${size}x$size/apps/tt-research-terminal.png"
done
install -Dm644 "$assets/tt-research.svg" "$share/icons/hicolor/scalable/apps/tt-research-terminal.svg"
mkdir -p "$share/applications"
sed "s|@BIN@|$HOME/.local/bin/obb-terminal|" "$root/packaging/obb-terminal.desktop.in" > "$share/applications/obb-terminal.desktop"
gtk-update-icon-cache -f -t "$share/icons/hicolor" 2>/dev/null || true
update-desktop-database "$share/applications" 2>/dev/null || true
echo "TT Research Terminal added to the desktop menu."
