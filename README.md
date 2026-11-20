# TT Research Terminal

A desktop research terminal for stocks and funds: quotes, charts, fundamentals, a valuation
page with a blended fair value, a watchlist with buy-target alerts, daily dashboards, a cheapest
stocks screen (B3, UK, US) and an optional AI analyst that runs on your own Claude Code login.
Built with PySide6 on top of [OpenBB](https://openbb.co) and free Yahoo Finance data.

This is a research tool, not investment advice.

## Download

Get the latest test build from **[Releases](https://github.com/tolenuk/tt-research-terminal/releases/latest)**:

| Platform | File | How to install |
|---|---|---|
| **macOS 14 or newer** | `TT-Research-Terminal-macOS-<version>.zip` | Unpack it and follow `TESTERS.txt` inside: one Terminal command, no Gatekeeper warning |
| **Windows 10 / 11** | `TT-Research-Terminal-Setup-<version>.exe` | Run it. It is not code-signed: choose "More info", then "Run anyway" |

No GitHub account is needed to download.

## From source

- macOS: [packaging/macos/README.md](packaging/macos/README.md)
- Windows: [packaging/windows/README.md](packaging/windows/README.md)
- Linux: `uv tool install --python 3.12 .`, then `packaging/install-linux-launcher.sh`

The AI analyst needs [Claude Code](https://claude.ai/code) installed and logged in with your
own Claude account; everything else works without it.

## Licence

[MIT](LICENSE). The market data comes from Yahoo Finance and is subject to its terms.
