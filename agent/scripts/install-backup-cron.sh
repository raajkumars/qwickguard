#!/usr/bin/env bash
set -euo pipefail

# QwickGuard Cron Installer
# Idempotently installs backup-related cron entries using # QwickGuard markers

BACKUP_SCRIPT="${HOME}/.qwickguard/scripts/backup.sh"
VERIFY_SCRIPT="${HOME}/.qwickguard/scripts/verify-backups.sh"
LOG_DIR="${HOME}/.qwickguard/logs"
CRON_MARKER="# QwickGuard"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

# Check backup.sh exists
if [[ ! -f "${BACKUP_SCRIPT}" ]]; then
  echo "ERROR: ${BACKUP_SCRIPT} not found. Deploy backup.sh first." >&2
  exit 1
fi

log "Removing existing QwickGuard cron entries..."

# Get current crontab, strip existing QwickGuard block, install new entries
# Use a temp file to build new crontab
TMPFILE=$(mktemp)
trap 'rm -f "${TMPFILE}"' EXIT

# Capture current crontab (may be empty — crontab -l exits non-zero if none)
crontab -l 2>/dev/null | grep -v "${CRON_MARKER}" | grep -v "backup.sh" | grep -v "verify-backups.sh" | grep -v "qwickguard.*logs" > "${TMPFILE}" || true

# Append QwickGuard entries
cat >> "${TMPFILE}" << CRON_ENTRIES

${CRON_MARKER}
# Run backup every 6 hours
0 */6 * * * ${BACKUP_SCRIPT} >> ${LOG_DIR}/backup.log 2>&1 ${CRON_MARKER}
# Delete logs older than 30 days (weekly, Sunday 3am)
0 3 * * 0 find ${LOG_DIR} -name "*.log" -mtime +30 -delete ${CRON_MARKER}
# Verify backups weekly (Sunday 4am)
0 4 * * 0 ${VERIFY_SCRIPT} >> ${LOG_DIR}/backup.log 2>&1 ${CRON_MARKER}
CRON_ENTRIES

# Install updated crontab
crontab "${TMPFILE}"

log "Cron entries installed. Installed QwickGuard entries:"
crontab -l | grep "${CRON_MARKER}" -A 1 | grep -v "^--$" || true

log "Full QwickGuard cron block:"
crontab -l | grep -A 10 "^${CRON_MARKER}$" || true
