#!/bin/bash
# Install, update or remove TT Research Terminal for the current macOS user (see README.md).
#
#   bash install.sh               install or update
#   bash install.sh --uninstall   remove the app and the background-alerts agent, keep your data
#
# Works from the source folder (packaging/macos/install.sh) or from the release tarball, which
# carries the wheel and the hash-pinned requirements next to this script. Builds
# ~/Applications/TT Research Terminal.app with its own Python 3.12 venv inside. No sudo.
# Written for the bash 3.2 that ships with macOS.
set -euo pipefail

APP_NAME="TT Research Terminal"
APPS_DIR="${TT_APPS_DIR:-$HOME/Applications}"
APP="$APPS_DIR/$APP_NAME.app"
LABEL="com.ttresearch.obb-terminal-alerts"  # alerts/launchd.py LABEL
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"
DATA="$HOME/Library/Application Support/obb-terminal"
UV_VERSION="0.13.6"
MIN_MACOS=14  # PySide6 6.12 wheels need macOS 14
HERE="$(cd "$(dirname "$0")" && pwd)"
VENV_PY="Contents/Resources/venv/bin/python"

say() { printf '%s\n' "$*"; }
warn() { printf 'Warning: %s\n' "$*" >&2; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = Darwin ] || die "This installer is for macOS."

# The app only: the alerts checker runs the same python with -m obb_terminal.alerts.checker.
app_running() { pgrep -f "$APP/$VENV_PY -m obb_terminal\$" >/dev/null 2>&1; }

remove_agent() {
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$AGENT"
}

if [ "${1:-}" = "--uninstall" ]; then
    app_running && die "Quit $APP_NAME first."
    if [ -f "$AGENT" ]; then
        remove_agent
        say "Removed the background-alerts agent."
    fi
    rm -rf "$APP" "$APP.previous"
    say "$APP_NAME removed. Your watchlist, journal and reports are kept in $DATA."
    exit 0
fi
[ $# -eq 0 ] || die "Usage: bash install.sh [--uninstall]"

major="$(sw_vers -productVersion | cut -d. -f1)"
[ "$major" -ge "$MIN_MACOS" ] || die "macOS $MIN_MACOS (Sonoma) or newer is needed; this Mac runs $(sw_vers -productVersion)."
app_running && die "Quit $APP_NAME first, then run this again."

# --- uv ---------------------------------------------------------------------------------------
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    say "Installing uv $UV_VERSION (Python package manager) from astral.sh ..."
    curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | env UV_NO_MODIFY_PATH=1 sh
    command -v uv >/dev/null 2>&1 || die "uv was installed but cannot be found in ~/.local/bin."
fi

# --- what to install: the release tarball's files, or a fresh build from the source folder ------
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
wheel="$(ls "$HERE"/obb_terminal-*.whl 2>/dev/null | tail -n 1 || true)"
if [ -n "$wheel" ] && [ -f "$HERE/requirements.txt" ]; then
    reqs="$HERE/requirements.txt"
elif [ -f "$HERE/../../pyproject.toml" ]; then
    root="$(cd "$HERE/../.." && pwd)"
    say "Building from $root ..."
    (cd "$root" && uv export --frozen --no-dev --no-emit-project --quiet -o "$WORK/requirements.txt") \
        || die "uv export failed."
    uv build "$root" --wheel --out-dir "$WORK" --quiet || die "uv build failed."
    reqs="$WORK/requirements.txt"
    wheel="$(ls "$WORK"/obb_terminal-*.whl | tail -n 1)"
else
    die "No wheel + requirements.txt next to this script, and no source folder around it."
fi
version="$(basename "$wheel" | cut -d- -f2)"

# --- build the app bundle; keep the old one until the new one works -----------------------------
write_bundle() {
    local app="$1"
    mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources" || return 1
    cat > "$app/Contents/Info.plist" <<PLIST || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>$APP_NAME</string>
    <key>CFBundleDisplayName</key><string>$APP_NAME</string>
    <key>CFBundleIdentifier</key><string>com.ttresearch.terminal</string>
    <key>CFBundleExecutable</key><string>tt-research-terminal</string>
    <key>CFBundleIconFile</key><string>tt-research</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$version</string>
    <key>CFBundleVersion</key><string>$version</string>
    <key>LSMinimumSystemVersion</key><string>$MIN_MACOS.0</string>
    <key>LSApplicationCategoryType</key><string>public.app-category.finance</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST
    # Finder starts apps with PATH=/usr/bin:/bin:/usr/sbin:/sbin; the AI analyst needs `claude`.
    cat > "$app/Contents/MacOS/tt-research-terminal" <<'LAUNCHER' || return 1
#!/bin/bash
contents="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
exec "$contents/Resources/venv/bin/python" -m obb_terminal "$@"
LAUNCHER
    chmod 755 "$app/Contents/MacOS/tt-research-terminal" || return 1
    say "Installing $APP_NAME $version (the first run downloads Python 3.12 and the libraries) ..."
    uv venv --quiet --managed-python --python 3.12 "$app/Contents/Resources/venv" || return 1
    uv pip install --quiet --python "$app/$VENV_PY" --require-hashes -r "$reqs" || return 1
    uv pip install --quiet --python "$app/$VENV_PY" --no-deps "$wheel" || return 1
    cp "$app/Contents/Resources/venv/lib/python3.12/site-packages/obb_terminal/ui/assets/tt-research.icns" \
        "$app/Contents/Resources/tt-research.icns" || return 1
    "$app/$VENV_PY" -c "import obb_terminal.app" || return 1
}

mkdir -p "$APPS_DIR"
# A run cut short mid-build (Ctrl-C, Terminal closed) leaves the good app in .previous and a
# half-built one in its place: keep whichever of the two works before starting again.
if [ -d "$APP.previous" ]; then
    if [ -d "$APP" ] && "$APP/$VENV_PY" -c "import obb_terminal.app" >/dev/null 2>&1; then
        rm -rf "$APP.previous"
    else
        rm -rf "$APP"
        mv "$APP.previous" "$APP"
    fi
fi
previous=""
if [ -d "$APP" ]; then
    previous="$APP.previous"
    rm -rf "$previous"
    mv "$APP" "$previous"
fi
if ! write_bundle "$APP"; then
    rm -rf "$APP"
    [ -n "$previous" ] && mv "$previous" "$APP"
    die "Install failed; nothing was changed."
fi
[ -n "$previous" ] && rm -rf "$previous"
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -f "$APP" 2>/dev/null || true
say "App: $APP"

# An existing alerts agent points at the venv's python; register it again for the new venv.
if [ -f "$AGENT" ]; then
    "$APP/$VENV_PY" -m obb_terminal.alerts.checker --install || warn "Could not re-register background alerts; switch them on again in the Watchlist tab."
fi

if command -v claude >/dev/null 2>&1 || [ -x "$HOME/.local/bin/claude" ] || [ -x /opt/homebrew/bin/claude ]; then
    say "Claude Code: found (run 'claude' once in Terminal to log in, if you have not)."
else
    warn "Claude Code not found, so the AI analyst will be unavailable. Install it with
    curl -fsSL https://claude.ai/install.sh | bash
then run 'claude' once to log in."
fi

say ""
say "Done. Open \"$APP_NAME\" from ~/Applications (Finder → Go → Home → Applications), Launchpad or Spotlight."
