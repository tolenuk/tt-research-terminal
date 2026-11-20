# TT Research Terminal on macOS

macOS 14 (Sonoma) or newer, Apple silicon or Intel, per user. No administrator rights and no
Apple Developer ID are needed.

## Install

From a clone of the source, in Terminal:

```bash
git clone https://github.com/tolenuk/tt-research-terminal.git
bash obb-terminal/packaging/macos/install.sh
```

Testers without access to the repo get the release zip instead (see below) and run the
`install.sh` inside it; [TESTERS.txt](TESTERS.txt) walks them through it.

The script:

- installs [uv](https://docs.astral.sh/uv/) into `~/.local/bin` if it is missing (it fetches a
  uv-managed Python 3.12 itself, so Homebrew upgrades cannot break the app)
- builds `~/Applications/TT Research Terminal.app`, with its own venv in
  `Contents/Resources/venv`, from the hash-pinned `uv.lock` (the release zip carries the same
  pins as `requirements.txt`)
- keeps the previous app until the new one has been built and imports cleanly
- registers the background-alerts agent again if it was on
- checks for Claude Code

The app is built on the Mac by the script rather than downloaded, so Gatekeeper never
quarantines it and no "unidentified developer" warning appears. To update, `git pull` and run
the script again (quit the app first). `--uninstall` removes the app and the alerts agent and
keeps your data.

## Claude Code (for the AI analyst)

```bash
curl -fsSL https://claude.ai/install.sh | bash
claude   # once, to log in
```

Finder and launchd start programs with `PATH=/usr/bin:/bin:/usr/sbin:/sbin`, so the app's
launcher adds `~/.local/bin`, `/opt/homebrew/bin` and `/usr/local/bin`, and the app also looks
for `claude` in those folders. A different location can go in `config.toml` as `claude_path`.

## Where things live

| What | macOS |
|---|---|
| The app and its venv | `~/Applications/TT Research Terminal.app` |
| Watchlist, targets, journal, AI reports, log | `~/Library/Application Support/obb-terminal` |
| `config.toml` (AI model, effort, `claude_path`) | `~/.config/obb-terminal` (as on Linux) |
| PDF reports | `~/Documents/TT Research Reports` |
| Background-alerts agent | `~/Library/LaunchAgents/com.ttresearch.obb-terminal-alerts.plist` |
| Background-alerts output (crashes only; the checker logs to the app log) | `obb-terminal-alerts.stdio.log` in the data folder |

## Background alerts

The Watchlist tab's **Background alerts** switch installs a launchd user agent that runs
Monday to Friday, 07:00–21:45, every 15 minutes, while you are logged in. macOS may announce
it as a new background item; turning it off under System Settings → General → Login Items
stops it too. Notifications come from "Script Editor" (they are sent with `osascript`), so
allow that app under System Settings → Notifications. From Terminal:

```bash
PY="$HOME/Applications/TT Research Terminal.app/Contents/Resources/venv/bin/python"
"$PY" -m obb_terminal.alerts.checker --status    # or --install / --uninstall, or no flag for one check
```

## Release zip

`.github/workflows/macos.yml` runs the test suite on a macOS runner (including a real launchd
round trip and a real `osascript` call), then packs
`TT-Research-Terminal-macOS-<version>.zip` (install.sh, wheel, requirements.txt, TESTERS.txt),
test-installs it, starts the app, checks the alerts command and uninstalls it. Run it from the
Actions tab for a test build, or push a `v<version>` tag: the zip is added to the same
GitHub release as the Windows installer.

## Icon

`ui/assets/tt-research.icns` is generated from the PNGs:
`uv run python packaging/macos/make_icns.py`.
