# TT Research Terminal on Windows

Windows 10 or 11, per user, no administrator rights needed.

## 1. Get the source

The code is in the public repo
[tolenuk/tt-research-terminal](https://github.com/tolenuk/tt-research-terminal). Pick one of
these:

- **With Git** (makes updates easy): install [Git for Windows](https://git-scm.com/download/win),
  then in PowerShell:

  ```powershell
  cd $env:USERPROFILE
  git clone https://github.com/tolenuk/tt-research-terminal.git
  cd tt-research-terminal
  ```


- **Without Git:** open the repo on github.com, choose **Code → Download ZIP**,
  and unzip it (for example to `C:\Users\<you>\tt-research-terminal`).

## 2. Install

In PowerShell, from the `obb-terminal` folder:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\install.ps1
```

The script:

- installs [uv](https://docs.astral.sh/uv/) if it is missing (it fetches Python 3.12 itself)
- installs TT Research Terminal as a uv tool
- adds an **TT Research Terminal** shortcut to the Start Menu (no console window)
- checks for Claude Code

To update, run `git pull` in the folder (or download a fresh ZIP), then run the same
command again. To remove everything except your data, add `-Uninstall`.

## 3. Claude Code (for the AI analyst)

Install the **native** Claude Code, then run `claude` once to log in:

```powershell
irm https://claude.ai/install.ps1 | iex
claude
```

The npm version (`claude.cmd`) is refused, because `cmd.exe` would mangle the arguments.

## Where things live

| What | Windows | Linux |
|---|---|---|
| Watchlist, targets, journal, AI reports, log | `%LOCALAPPDATA%\obb-terminal` | `~/.local/share/obb-terminal` |
| `config.toml` (AI model, effort, `claude_path`) | `%APPDATA%\obb-terminal` | `~/.config/obb-terminal` |
| PDF reports | `Documents\TT Research Reports` | `~/Documents/TT Research Reports` |
| Background-alerts checker log | `%LOCALAPPDATA%\obb-terminal\obb-terminal-alerts.log` | shared with the app log |

## Background alerts

The Watchlist tab's **Background alerts** switch registers a Task Scheduler task called
*OBB Terminal Alerts*. It runs Monday to Friday, 07:00–21:45, every 15 minutes, and only
while you are logged in. Alerts appear as Windows notifications from "Windows PowerShell".
From a terminal you can also run `obb-terminal-alerts --install`, `--uninstall` or
`--status`.

## Installer for testers

`installer/` holds a one-file installer (`TT-Research-Terminal-Setup-<version>.exe`) for people without
access to this repo. The exe carries the app and uv. During install it fetches Python 3.12 and the
exact library versions from `uv.lock` (hash-checked), all inside
`%LOCALAPPDATA%\Programs\obb-terminal`, and can also install Claude Code and Git for Windows.

GitHub Actions builds it on Windows (`.github/workflows/windows-installer.yml`). Each run
also runs the test suite on Windows, test-installs the exe, starts the app and uninstalls it:

- **Test build:** Actions tab → *Windows installer* → *Run workflow*. The exe is in the run's artifacts.
- **Release:** bump `version` in `pyproject.toml`, commit, then `git tag v<version> && git push origin v<version>`.
  The exe is attached to a GitHub release; testers download it from the Releases page.
