#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
QG_HOME="$HOME/.qwickguard"
VENV_PATH="$QG_HOME/venv"
SERVICE_NAME="qwickguard-agent"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

echo "=== QwickGuard Agent Installer (Linux) ==="

# 1. Create directory structure
echo "[1/6] Creating directories..."
mkdir -p "$QG_HOME"/{logs,backups/faabzi-postgres,backups/qwickbrain-postgres,scripts,flags,report_queue}

# 2. Create Python venv and install
echo "[2/6] Setting up Python environment..."
PYTHON3=""
for py in python3.13 python3.12 python3.11; do
    if command -v "$py" &>/dev/null; then
        PYTHON3="$py"
        break
    fi
done
if [ -z "$PYTHON3" ]; then
    echo "ERROR: Python 3.11+ required. Install python3.11 and python3.11-venv via your package manager."
    exit 1
fi
echo "  Using $PYTHON3 ($($PYTHON3 --version))"
"$PYTHON3" -m venv "$VENV_PATH"
"$VENV_PATH/bin/pip" install --upgrade pip --quiet
"$VENV_PATH/bin/pip" install -e "$AGENT_DIR" --quiet

# 3. Copy scripts
echo "[3/6] Installing scripts..."
for script in backup.sh verify-backups.sh install-backup-cron.sh; do
    if [ -f "$AGENT_DIR/scripts/$script" ]; then
        cp "$AGENT_DIR/scripts/$script" "$QG_HOME/scripts/"
        chmod +x "$QG_HOME/scripts/$script"
    fi
done

# 4. Install systemd user unit
echo "[4/6] Installing systemd user service..."
CONFIG_PATH="${QWICKGUARD_CONFIG:-$(cd "$AGENT_DIR/.." && pwd)/configs/$(hostname).yaml}"
mkdir -p "$SYSTEMD_USER_DIR"
sed -e "s|__VENV_PATH__|$VENV_PATH|g" \
    -e "s|__CONFIG_PATH__|$CONFIG_PATH|g" \
    "$AGENT_DIR/templates/$SERVICE_NAME.service" > "$SYSTEMD_USER_DIR/$SERVICE_NAME.service"

# Enable lingering so user services run without an active login session
if command -v loginctl &>/dev/null; then
    loginctl enable-linger "$USER" 2>/dev/null || echo "  Warning: Could not enable linger. Service may stop when you log out."
fi

# 5. Install backup cron
echo "[5/6] Installing backup cron..."
if [ -f "$QG_HOME/scripts/install-backup-cron.sh" ]; then
    "$QG_HOME/scripts/install-backup-cron.sh"
else
    echo "  Skipping (install-backup-cron.sh not found)"
fi

# 6. Enable and start agent
echo "[6/6] Starting agent..."
if ! systemctl --user status &>/dev/null; then
    echo "ERROR: systemd user session not available. Is systemd running?"
    exit 1
fi
systemctl --user daemon-reload
systemctl --user enable --now "$SERVICE_NAME"

echo ""
echo "=== Installation complete ==="
echo "Agent status: $(systemctl --user is-active "$SERVICE_NAME" 2>/dev/null || echo 'checking...')"
echo "Logs: journalctl --user -u $SERVICE_NAME -f"
echo "Config: $CONFIG_PATH"
echo "Venv: $VENV_PATH"
echo "Unit: $SYSTEMD_USER_DIR/$SERVICE_NAME.service"
