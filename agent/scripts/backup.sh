#!/usr/bin/env bash
set -euo pipefail

# QwickGuard Backup Script
# Dumps both Postgres containers, verifies size, rotates old backups

BACKUP_DIR="${HOME}/.qwickguard/backups"
LOG_FILE="${HOME}/.qwickguard/logs/backup.log"
MIN_BACKUP_SIZE=1024  # 1KB minimum
RETENTION_DAYS=14

log() {
  local level="$1"
  shift
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] $*" | tee -a "${LOG_FILE}"
}

get_file_size() {
  local file="$1"
  # macOS uses stat -f%z, Linux uses stat -c%s
  if stat -f%z "${file}" >/dev/null 2>&1; then
    stat -f%z "${file}"
  else
    stat -c%s "${file}"
  fi
}

is_container_running() {
  local container="$1"
  local state
  state=$(docker inspect --format='{{.State.Running}}' "${container}" 2>/dev/null || echo "false")
  [[ "${state}" == "true" ]]
}

backup_container() {
  local container="$1"
  local db_user="$2"
  local container_backup_dir="${BACKUP_DIR}/${container}"
  local timestamp
  timestamp=$(date '+%Y-%m-%d_%H%M')
  local backup_file="${container_backup_dir}/${timestamp}.sql.gz"

  log "INFO" "Starting backup for ${container} (user: ${db_user})"

  # Check container is running
  if ! is_container_running "${container}"; then
    log "ERROR" "Container ${container} is not running — skipping backup"
    return 1
  fi

  # Run pg_dumpall and compress
  log "INFO" "Running pg_dumpall for ${container} -> ${backup_file}"
  if ! docker exec "${container}" pg_dumpall -U "${db_user}" | gzip > "${backup_file}"; then
    log "ERROR" "pg_dumpall failed for ${container}"
    rm -f "${backup_file}"
    return 1
  fi

  # Verify backup file size
  if [[ ! -f "${backup_file}" ]]; then
    log "ERROR" "Backup file not created for ${container}: ${backup_file}"
    return 1
  fi

  local file_size
  file_size=$(get_file_size "${backup_file}")
  log "INFO" "Backup file size for ${container}: ${file_size} bytes"

  if [[ "${file_size}" -lt "${MIN_BACKUP_SIZE}" ]]; then
    log "ERROR" "Backup file too small for ${container}: ${file_size} < ${MIN_BACKUP_SIZE} bytes — removing suspect file"
    rm -f "${backup_file}"
    return 1
  fi

  log "INFO" "Backup successful for ${container}: ${backup_file} (${file_size} bytes)"

  # Rotate backups older than RETENTION_DAYS
  log "INFO" "Rotating backups older than ${RETENTION_DAYS} days for ${container}"
  local deleted_count
  deleted_count=$(find "${container_backup_dir}" -name "*.sql.gz" -mtime "+${RETENTION_DAYS}" -print | wc -l | tr -d ' ')
  find "${container_backup_dir}" -name "*.sql.gz" -mtime "+${RETENTION_DAYS}" -delete
  if [[ "${deleted_count}" -gt 0 ]]; then
    log "INFO" "Rotated ${deleted_count} old backup(s) for ${container}"
  fi

  return 0
}

main() {
  log "INFO" "=== QwickGuard backup run started ==="
  local overall_exit=0

  # Container name -> DB user mappings (use parallel arrays to avoid hyphen-in-key issues)
  local containers=("faabzi-postgres" "qwickbrain-postgres")
  local db_users=("postgres" "qwickbrain")

  for i in "${!containers[@]}"; do
    local container="${containers[$i]}"
    local db_user="${db_users[$i]}"
    if ! backup_container "${container}" "${db_user}"; then
      log "ERROR" "Backup FAILED for ${container}"
      overall_exit=1
    fi
  done

  if [[ "${overall_exit}" -eq 0 ]]; then
    log "INFO" "=== All backups completed successfully ==="
  else
    log "ERROR" "=== Backup run completed with errors ==="
  fi

  exit "${overall_exit}"
}

main
