#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
QG_HOME="$HOME/.qwickguard"
VENV_PATH="$QG_HOME/venv"
PLIST_NAME="com.qwickapps.qwickguard-agent"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"

echo "=== QwickGuard Agent Installer ==="

# 1. Create directory structure
echo "[1/6] Creating directories..."
mkdir -p "$QG_HOME"/{logs,backups/faabzi-postgres,backups/qwickbrain-postgres,scripts,flags,report_queue}

# 2. Create Python venv and install
echo "[2/6] Setting up Python environment..."
python3 -m venv "$VENV_PATH"
"$VENV_PATH/bin/pip" install --upgrade pip --quiet
"$VENV_PATH/bin/pip" install -e "$AGENT_DIR" --quiet

# 3. Copy scripts (from M1 backup scripts already in agent/scripts/)
echo "[3/6] Installing scripts..."
for script in backup.sh verify-backups.sh install-backup-cron.sh; do
    if [ -f "$AGENT_DIR/scripts/$script" ]; then
        cp "$AGENT_DIR/scripts/$script" "$QG_HOME/scripts/"
        chmod +x "$QG_HOME/scripts/$script"
    fi
done

# 4. Install LaunchAgent plist
echo "[4/6] Installing LaunchAgent..."
CONFIG_PATH="${QWICKGUARD_CONFIG:-$(cd "$AGENT_DIR/.." && pwd)/configs/macmini-devserver.yaml}"
mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__VENV_PATH__|$VENV_PATH|g" \
    -e "s|__CONFIG_PATH__|$CONFIG_PATH|g" \
    -e "s|__HOME__|$HOME|g" \
    "$AGENT_DIR/templates/$PLIST_NAME.plist" > "$PLIST_DEST"

# 5. Install backup cron (if installer exists)
echo "[5/6] Installing backup cron..."
if [ -f "$QG_HOME/scripts/install-backup-cron.sh" ]; then
    "$QG_HOME/scripts/install-backup-cron.sh"
else
    echo "  Skipping (install-backup-cron.sh not found)"
fi

# 6. Load and start agent
echo "[6/6] Starting agent..."
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"

echo ""
echo "=== Installation complete ==="
echo "Agent status: $(launchctl list 2>/dev/null | grep $PLIST_NAME || echo 'checking...')"
echo "Logs: $QG_HOME/logs/"
echo "Config: $CONFIG_PATH"
echo "Venv: $VENV_PATH"
