#!/usr/bin/env bash
set -euo pipefail

# QwickGuard Backup Verification Script
# Checks recency, gzip integrity, and SQL structure for each container's backups
# Sets flag files for the agent to detect problems

BACKUP_DIR="${HOME}/.qwickguard/backups"
FLAGS_DIR="${HOME}/.qwickguard/flags"
LOG_FILE="${HOME}/.qwickguard/logs/backup.log"
MAX_AGE_HOURS=12

log() {
  local level="$1"
  shift
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [VERIFY] [${level}] $*" | tee -a "${LOG_FILE}"
}

set_flag() {
  local flag="$1"
  local message="$2"
  local flag_file="${FLAGS_DIR}/${flag}"
  echo "${message}" > "${flag_file}"
  log "WARN" "Flag set: ${flag} — ${message}"
}

clear_flag() {
  local flag="$1"
  local flag_file="${FLAGS_DIR}/${flag}"
  if [[ -f "${flag_file}" ]]; then
    rm -f "${flag_file}"
    log "INFO" "Flag cleared: ${flag}"
  fi
}

verify_container() {
  local container="$1"
  local container_backup_dir="${BACKUP_DIR}/${container}"
  local result=0

  log "INFO" "Verifying backups for ${container}"

  # --- Recency check ---
  # Find the latest backup file
  local latest_backup
  latest_backup=$(find "${container_backup_dir}" -name "*.sql.gz" -type f 2>/dev/null | sort | tail -n1 || true)

  if [[ -z "${latest_backup}" ]]; then
    log "ERROR" "No backup files found for ${container}"
    set_flag "backup-missing-${container}" "No backup files found in ${container_backup_dir}"
    clear_flag "backup-stale-${container}" 2>/dev/null || true
    clear_flag "backup-corrupt-${container}" 2>/dev/null || true
    return 1
  fi

  # Check age of latest backup
  local file_mtime
  local now_epoch
  local file_epoch
  now_epoch=$(date '+%s')

  # macOS stat uses -f%m, Linux uses -c%Y
  if stat -f%m "${latest_backup}" >/dev/null 2>&1; then
    file_epoch=$(stat -f%m "${latest_backup}")
  else
    file_epoch=$(stat -c%Y "${latest_backup}")
  fi

  local age_seconds=$(( now_epoch - file_epoch ))
  local age_hours=$(( age_seconds / 3600 ))
  local max_age_seconds=$(( MAX_AGE_HOURS * 3600 ))

  log "INFO" "Latest backup for ${container}: ${latest_backup} (age: ${age_hours}h)"

  if [[ "${age_seconds}" -gt "${max_age_seconds}" ]]; then
    log "ERROR" "Latest backup for ${container} is stale: ${age_hours}h old (max: ${MAX_AGE_HOURS}h)"
    set_flag "backup-stale-${container}" "Latest backup is ${age_hours}h old (max: ${MAX_AGE_HOURS}h): ${latest_backup}"
    result=1
  else
    log "INFO" "Recency check passed for ${container}: ${age_hours}h old"
    clear_flag "backup-stale-${container}"
  fi

  # --- Gzip integrity check ---
  if ! gunzip -t "${latest_backup}" >/dev/null 2>&1; then
    log "ERROR" "Gzip integrity check FAILED for ${container}: ${latest_backup}"
    set_flag "backup-corrupt-${container}" "Gzip integrity check failed: ${latest_backup}"
    return 1
  fi
  log "INFO" "Gzip integrity check passed for ${container}"

  # --- SQL structure check ---
  # Decompress first 20 lines and look for pg_dumpall markers
  local sql_header
  sql_header=$(gunzip -c "${latest_backup}" 2>/dev/null | head -n 20 || true)

  local structure_ok=0
  for marker in "PostgreSQL" "pg_dumpall" "CREATE" "ALTER"; do
    if echo "${sql_header}" | grep -q "${marker}"; then
      structure_ok=1
      break
    fi
  done

  if [[ "${structure_ok}" -eq 0 ]]; then
    log "ERROR" "SQL structure check FAILED for ${container}: no expected pg_dump markers in first 20 lines"
    set_flag "backup-corrupt-${container}" "SQL structure check failed — no PostgreSQL/pg_dumpall/CREATE/ALTER markers found: ${latest_backup}"
    result=1
  else
    log "INFO" "SQL structure check passed for ${container}"
    clear_flag "backup-corrupt-${container}"
  fi

  # --- Clear missing flag (backup exists, all other checks also ran) ---
  clear_flag "backup-missing-${container}"

  if [[ "${result}" -eq 0 ]]; then
    log "INFO" "All checks PASSED for ${container}"
  else
    log "ERROR" "One or more checks FAILED for ${container}"
  fi

  return "${result}"
}

main() {
  log "INFO" "=== QwickGuard backup verification started ==="
  local overall_exit=0

  local containers=("faabzi-postgres" "qwickbrain-postgres")

  for container in "${containers[@]}"; do
    if ! verify_container "${container}"; then
      overall_exit=1
    fi
  done

  if [[ "${overall_exit}" -eq 0 ]]; then
    log "INFO" "=== All verification checks PASSED ==="
  else
    log "ERROR" "=== Verification completed with FAILURES — check flags in ${FLAGS_DIR} ==="
  fi

  exit "${overall_exit}"
}

main
