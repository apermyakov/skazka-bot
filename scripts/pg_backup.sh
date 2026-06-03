#!/bin/bash
set -euo pipefail

BACKUP_DIR=/opt/skazka-bot/backups
RETENTION_DAYS=14
STAMP=$(date +%F-%H%M)
OUT="$BACKUP_DIR/skazka-$STAMP.sql.gz"

mkdir -p "$BACKUP_DIR"
docker exec skazka-postgres pg_dump -U skazka skazka | gzip > "$OUT"

if [ ! -s "$OUT" ]; then
  echo "[$(date -Iseconds)] ERROR: empty dump $OUT" >&2
  rm -f "$OUT"
  exit 1
fi

echo "[$(date -Iseconds)] OK $(du -h "$OUT" | cut -f1) $OUT"
find "$BACKUP_DIR" -name 'skazka-*.sql.gz' -mtime +$RETENTION_DAYS -delete
