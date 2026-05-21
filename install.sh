#!/usr/bin/env bash
# install.sh — sets up Codex Usage Monitor on Debian
# Run once: bash install.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTOSTART_DIR="$HOME/.config/autostart"
CRON_INTERVAL=30   # minutes between monitor refreshes

echo "=== Codex + Claude Usage Monitor — installer ==="

# ── 1. system packages (GTK indicator backend for pystray) ────────────────────
echo "[1/4] Checking system packages …"
MISSING=()
for pkg in python3-gi python3-gi-cairo gir1.2-ayatanaappindicator3-0.1 libayatana-appindicator3-1 python3-pip; do
    dpkg -s "$pkg" &>/dev/null || MISSING+=("$pkg")
done

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "      Installing: ${MISSING[*]}"
    sudo apt-get install -y "${MISSING[@]}"
else
    echo "      System packages already present."
fi

# ── 2. Python dependencies ────────────────────────────────────────────────────
echo "[2/4] Installing Python dependencies …"
pip3 install --user --break-system-packages -r "$REPO_DIR/requirements.txt" --quiet

# ── 3. cron job (monitor runs every $CRON_INTERVAL minutes) ──────────────────
echo "[3/4] Setting up cron jobs (every ${CRON_INTERVAL}m) …"
mkdir -p "$HOME/.codex-usage" "$HOME/.claude-usage"
CRON_CODEX="*/$CRON_INTERVAL * * * * python3 $REPO_DIR/codex_monitor.py >> $HOME/.codex-usage/monitor.log 2>&1"
CRON_CLAUDE="*/$CRON_INTERVAL * * * * python3 $REPO_DIR/claude_monitor.py >> $HOME/.claude-usage/monitor.log 2>&1"
# Remove old entries, add new ones
( crontab -l 2>/dev/null | grep -v "codex_monitor.py\|claude_monitor.py" || true
  echo "$CRON_CODEX"
  echo "$CRON_CLAUDE"
) | crontab -
echo "      Cron jobs added."

# ── 4. autostart entry for the tray widget ────────────────────────────────────
echo "[4/4] Creating autostart entry …"
mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_DIR/codex-tray.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Codex Usage Tray
Exec=python3 $REPO_DIR/codex_tray.py
Icon=utilities-system-monitor
Comment=Shows Codex CLI usage limits in the system tray
Categories=Utility;
X-GNOME-Autostart-enabled=true
StartupNotify=false
Hidden=false
EOF
echo "      Autostart entry created."

echo ""
echo "=== Done! ==="
echo ""
echo "  Start the tray widget now:       python3 $REPO_DIR/codex_tray.py &"
echo "  Force a manual refresh:          python3 $REPO_DIR/codex_monitor.py"
echo "                                   python3 $REPO_DIR/claude_monitor.py"
echo "  Or click 'Refresh now' in the tray menu."
echo ""
echo "  Both monitors run automatically every ${CRON_INTERVAL} minutes via cron."
echo "  Cron logs: ~/.codex-usage/monitor.log  ~/.claude-usage/monitor.log"
echo ""
echo "  The tray widget will start automatically at next login."
