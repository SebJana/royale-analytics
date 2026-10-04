#!/bin/bash
set -euo pipefail 2>/dev/null || set -eu

MONGO_HOST="$MONGO_HOST"
MONGO_PORT="$MONGO_PORT"
MONGO_DB="$MONGO_APP_DB"
MONGO_USER="$MONGO_APP_USER"
MONGO_PWD="$MONGO_APP_PWD"
MONGO_AUTH_DB="$MONGO_APP_DB"

BACKUP_DIR="/backups"
RETENTION_DAYS="$BACKUP_RETENTION_DAYS"
CRON_HOUR="$BACKUP_HOUR"
CRON_MINUTE="$BACKUP_MINUTE"

mkdir -p "$BACKUP_DIR"

backup_once() {
  TS="$(date +%F_%H-%M-%S)"
  OUT_DIR="${BACKUP_DIR}/${MONGO_DB}_${TS}"
  echo "[backup] starting dump to ${OUT_DIR}"

  mongodump \
    --host "${MONGO_HOST}" \
    --port "${MONGO_PORT}" \
    --username "${MONGO_USER}" \
    --password "${MONGO_PWD}" \
    --authenticationDatabase "${MONGO_AUTH_DB}" \
    --db "${MONGO_DB}" \
    --gzip \
    --out "${OUT_DIR}"

  echo "[backup] dump done"
  echo "[backup] pruning backups older than ${RETENTION_DAYS} days"
  find "${BACKUP_DIR}" -maxdepth 1 -type d -name "${MONGO_DB}_*" -mtime +"${RETENTION_DAYS}" -exec rm -rf {} \;
}

echo "[backup] initial backup run…"
backup_once || echo "[backup] initial run failed"

CRON_LINE="${CRON_MINUTE} ${CRON_HOUR} * * * /usr/bin/env bash -c '/app/backup.sh run' >> /var/log/cron.log 2>&1"
echo "$CRON_LINE" > /etc/crontabs/root

if [[ "${1:-}" == "run" ]]; then
  backup_once
  exit 0
fi

echo "[backup] starting crond in foreground"
crond -f -l 8
