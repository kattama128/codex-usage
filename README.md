# codex-usage

System tray widget that shows **Codex** and **Claude Code** usage limits as live progress bars.

![icon preview: four bars — C5h, Cwk, L5h, L7d]

## What it shows

| Bar | Meaning |
|-----|---------|
| **C5h** | Codex — % remaining in the current 5-hour window |
| **Cwk** | Codex — % remaining in the current weekly window |
| **L5h** | Claude Code — % remaining in the current 5-hour window |
| **L7d** | Claude Code — % remaining in the current 7-day window |

Hover for a tooltip with exact percentages and reset times. Right-click for the full detail menu and a manual **Refresh** button.

## How it works

**Codex** (`codex_monitor.py`) — spawns the Codex CLI, sends `/status`, and parses the progress bars from its TUI output.

**Claude Code** (`claude_monitor.py`) — makes a minimal API call to `api.anthropic.com/v1/messages` and reads the rate-limit utilization from the response headers:
```
anthropic-ratelimit-unified-5h-utilization: 0.21
anthropic-ratelimit-unified-7d-utilization: 0.04
```
The OAuth token is read from `~/.claude/.credentials.json`, which Claude Code maintains automatically.

Both monitors save a `status.json` file that the tray widget polls every 30 seconds.

## Requirements

- Linux with a system tray (XFCE, GNOME with AppIndicator extension, KDE, …)
- Python 3.10+
- Codex CLI installed and authenticated
- Claude Code installed and authenticated (`claude auth status`)

## Install

```bash
bash install.sh
```

The installer:
1. Installs system packages (`libayatana-appindicator3`, `python3-gi`, …)
2. Installs Python dependencies (`pystray`, `Pillow`, `requests`, `pexpect`)
3. Adds two cron jobs that refresh the status files every 30 minutes
4. Creates an autostart entry so the tray widget launches at login

## Manual usage

```bash
# Start the tray widget
python3 codex_tray.py &

# Fetch status once (also triggered by "Refresh now" in the tray menu)
python3 codex_monitor.py
python3 claude_monitor.py

# Fetch in a loop (alternative to cron)
python3 codex_monitor.py --loop 1800
python3 claude_monitor.py --loop 900
```

## File layout

```
codex_tray.py        # system tray widget — reads both status files
codex_monitor.py     # fetches Codex usage via CLI /status
claude_monitor.py    # fetches Claude Code usage via API headers
install.sh           # one-shot installer
requirements.txt     # Python dependencies

~/.codex-usage/status.json   # written by codex_monitor.py
~/.claude-usage/status.json  # written by claude_monitor.py
```

## Cron logs

```
~/.codex-usage/monitor.log
~/.claude-usage/monitor.log
```
