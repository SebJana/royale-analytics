#!/usr/bin/env bash
set -euo pipefail

DUMP_DIR="${1:-}"
if [[ -z "$DUMP_DIR" || ! -d "$DUMP_DIR" ]]; then
  echo "Usage: $0 ./backups/clash_royale_YYYY-MM-DD_HH-MM-SS"
  exit 1
fi

# Reads one value from ../.env, the last line that sets it. Tolerates spaces
# around "=", surrounding quotes, and CRLF line endings. Only the requested
# value is read: the file also holds API keys, which must never be printed.
env_value() {
  [[ -f ../.env ]] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" ../.env |
    tail -n 1 | tr -d '\r' | sed -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/'
}

# Same defaults as restore.ps1. Host and port are no .env entries; compose
# sets them for the backup container only.
MONGO_HOST="$(env_value MONGO_HOST)"
MONGO_HOST="${MONGO_HOST:-mongo}"
MONGO_PORT="$(env_value MONGO_PORT)"
MONGO_PORT="${MONGO_PORT:-27017}"
MONGO_DB="$(env_value MONGO_APP_DB)"
MONGO_DB="${MONGO_DB:-clash_royale}"
MONGO_USER="$(env_value MONGO_APP_USER)"
MONGO_USER="${MONGO_USER:-data_scraper}"
MONGO_PWD="$(env_value MONGO_APP_PWD)"
MONGO_AUTH_DB="$MONGO_DB"

if [[ -z "$MONGO_PWD" ]]; then
  echo "[restore] error: MONGO_APP_PWD is not set in ../.env"
  exit 1
fi

# Backups are written with mongodump --gzip. A dump without compressed files is
# refused here, before mongorestore drops any collection.
if [[ -z "$(find "$DUMP_DIR" -type f -name '*.bson.gz' -print -quit)" ]]; then
  echo "[restore] error: ${DUMP_DIR} contains no compressed (*.bson.gz) dump"
  exit 1
fi

echo "[restore] restoring ${DUMP_DIR} -> ${MONGO_DB} on ${MONGO_HOST}:${MONGO_PORT}"

docker run --rm \
  --network "clash-royale-analytics_cr-analytics" \
  -v "$(realpath "$DUMP_DIR")":/dump:ro \
  mongo:7.0 mongorestore \
    --host "${MONGO_HOST}" \
    --port "${MONGO_PORT}" \
    --username "${MONGO_USER}" \
    --password "${MONGO_PWD}" \
    --authenticationDatabase "${MONGO_AUTH_DB}" \
    --nsInclude="${MONGO_DB}.*" \
    --drop \
    --gzip \
    /dump

echo "[restore] done."
