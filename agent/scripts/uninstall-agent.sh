#!/usr/bin/env bash
set -euo pipefail

PLIST_NAME="com.qwickapps.qwickguard-agent"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"
QG_HOME="$HOME/.qwickguard"

echo "=== QwickGuard Agent Uninstaller ==="

# 1. Stop and unload LaunchAgent
echo "[1/3] Stopping agent..."
launchctl unload "$PLIST_PATH" 2>/dev/null || true
rm -f "$PLIST_PATH"

# 2. Remove QwickGuard cron entries
echo "[2/3] Removing cron entries..."
if crontab -l 2>/dev/null | grep -q "QwickGuard"; then
    crontab -l 2>/dev/null | grep -v "# QwickGuard" | crontab -
    echo "  Cron entries removed"
else
    echo "  No cron entries found"
fi

# 3. Remove venv only (preserve data)
echo "[3/3] Removing venv..."
rm -rf "$QG_HOME/venv"

echo ""
echo "=== Uninstall complete ==="
echo "Data preserved at: $QG_HOME/"
echo "To remove ALL data (backups, logs): rm -rf $QG_HOME"
