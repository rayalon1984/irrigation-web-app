#!/bin/bash
# backup_sqlite.sh – Irrigation SQLite daily backup with rotation and optional offsite sync
# Safe defaults, integrity checks, disk guardrail, lock, and optional Pushover alerting

set -euo pipefail

# ---------------- Configuration ----------------
APP_DIR="/home/pi/smart-home/irrigation"
DB="${APP_DIR}/irrigation.db"
BACKUP_DIR="${APP_DIR}/backups"
TMP_DIR="${APP_DIR}/.tmp"
LOCK_FILE="${APP_DIR}/.backup.lock"
ENV_FILE="${APP_DIR}/.env"                 # optional, for PUSHOVER_*, RCLONE_REMOTE, RCLONE_FLAGS

RETENTION_DAYS=7                           # rotate older than N days
MIN_FREE_PCT=10                            # skip backup if free disk space on /home below this percentage

# Optional rclone offsite sync
# set in .env:
#   RCLONE_REMOTE="remote:irrigation-backups"   # rclone destination
#   RCLONE_FLAGS="--transfers 2 --checkers 4"   # optional flags

# ---------------- Bootstrap ----------------
log() { logger -t irrigation-backup "$*"; }
notify_pushover() {
  # requires PUSHOVER_APP_TOKEN and PUSHOVER_USER_KEY in ENV_FILE
  if [[ -n "${PUSHOVER_APP_TOKEN:-}" && -n "${PUSHOVER_USER_KEY:-}" ]]; then
    curl -s -X POST https://api.pushover.net/1/messages.json \
      -d "token=${PUSHOVER_APP_TOKEN}" \
      -d "user=${PUSHOVER_USER_KEY}" \
      -d "message=$1" >/dev/null || true
  fi
}

# Export variables from .env if exists
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
fi

# Prevent concurrent runs
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  log "skip concurrent backup, lock in place"
  exit 0
fi

# Ensure tools exist
if ! command -v sqlite3 >/dev/null 2>&1; then
  log "sqlite3 not found"
  notify_pushover "Backup failed. sqlite3 not found"
  exit 1
fi

# Disk guardrail on /home
USE_PCT=$(df -P /home | awk 'NR==2{gsub("%","",$5); print $5}')
FREE_PCT=$((100 - USE_PCT))
if (( FREE_PCT < MIN_FREE_PCT )); then
  log "skipped backup, low disk space. free=${FREE_PCT}% threshold=${MIN_FREE_PCT}%"
  notify_pushover "Backup skipped due to low disk space. Free ${FREE_PCT}%"
  exit 0
fi

# Create dirs
mkdir -p "${BACKUP_DIR}" "${TMP_DIR}"

# ---------------- Backup ----------------
ts="$(date +'%Y%m%d-%H%M%S')"
tmp_db="${TMP_DIR}/irrigation-${ts}.db"
out_db="${BACKUP_DIR}/irrigation-${ts}.db"
out_gz="${out_db}.gz"

# Consistent snapshot using sqlite .backup
sqlite3 "${DB}" ".backup '${tmp_db}'"

# Validate integrity of the snapshot
check_tmp="$(sqlite3 "${tmp_db}" 'PRAGMA integrity_check;' || true)"
if [[ "${check_tmp}" != "ok" ]]; then
  log "integrity_check failed on snapshot: ${check_tmp}"
  notify_pushover "Backup failed. integrity_check error on snapshot"
  rm -f "${tmp_db}"
  exit 2
fi

# Move and compress
mv "${tmp_db}" "${out_db}"
gzip -9 "${out_db}"

# ---------------- Rotation ----------------
removed_list="$(mktemp)"
find "${BACKUP_DIR}" -name 'irrigation-*.db.gz' -type f -mtime +${RETENTION_DAYS} -print > "${removed_list}" || true
if [[ -s "${removed_list}" ]]; then
  while IFS= read -r f; do
    rm -f "$f" && log "removed old backup: $f"
  done < "${removed_list}"
fi
rm -f "${removed_list}"

# ---------------- Optional offsite sync via rclone ----------------
if [[ -n "${RCLONE_REMOTE:-}" ]]; then
  if command -v rclone >/dev/null 2>&1; then
    if rclone copy "${BACKUP_DIR}" "${RCLONE_REMOTE}" ${RCLONE_FLAGS:-} >/dev/null 2>&1; then
      log "rclone sync completed to ${RCLONE_REMOTE}"
    else
      log "rclone sync failed to ${RCLONE_REMOTE}"
      notify_pushover "Backup completed locally but rclone sync failed"
    fi
  else
    log "rclone not found, skipping offsite sync"
  fi
fi

# ---------------- Done ----------------
log "backup created ${out_gz}"
echo "${out_gz}"
exit 0
