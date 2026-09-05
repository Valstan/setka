#!/bin/bash
# Database backup script for SETKA project

set -euo pipefail

# Configuration
BACKUP_DIR="/home/valstan/SETKA/backup"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/setka_backup_$DATE.sql"

# Create backup directory if not exists
mkdir -p "$BACKUP_DIR"

# Load DATABASE_URL from server-side env (root-readable)
ENV_FILE="/etc/setka/setka.env"
DATABASE_URL="$(sudo -n awk 'BEGIN{FS=\"=\"} $1==\"DATABASE_URL\"{sub(/^DATABASE_URL=/, \"\"); print; exit}' \"$ENV_FILE\")"
if [[ -z "${DATABASE_URL}" ]]; then
  echo "❌ DATABASE_URL not found in ${ENV_FILE}"
  exit 1
fi

# Parse DATABASE_URL safely (avoid printing secrets)
read -r DB_USER DB_PASSWORD DB_HOST DB_PORT DB_NAME < <(python3 - <<'PY'
import os
import re
import sys
from urllib.parse import urlparse

raw = os.environ.get("DATABASE_URL", "")
if not raw:
    sys.exit(1)

parse_url = raw.replace("postgresql+asyncpg://", "postgresql://", 1)
u = urlparse(parse_url)

user = u.username or ""
pw = u.password or ""
host = u.hostname or "localhost"
port = u.port or 5432
db = (u.path or "/").lstrip("/")

if not (user and pw and db):
    sys.exit(2)

print(user, pw, host, port, db)
PY
)

export DATABASE_URL

# Backup database
echo "🔄 Creating database backup..."
PGPASSWORD="$DB_PASSWORD" pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" > "$BACKUP_FILE"

# Compress backup
echo "📦 Compressing backup..."
gzip "$BACKUP_FILE"
BACKUP_FILE="${BACKUP_FILE}.gz"

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "✅ Backup created: $BACKUP_FILE (Size: $SIZE)"

# Keep only last 7 backups
echo "🧹 Cleaning old backups (keeping last 7)..."
cd "$BACKUP_DIR"
ls -t setka_backup_*.sql.gz 2>/dev/null | tail -n +8 | xargs -r rm

BACKUPS_COUNT=$(ls -1 setka_backup_*.sql.gz 2>/dev/null | wc -l)
echo "📊 Total backups: $BACKUPS_COUNT"

# Фото рекламных клиентов (кабинет) — данные вне БД (PR 1.8 аудита 2026-09-05).
# Каталог — AD_UPLOAD_DIR из setka.env, иначе старый путь в дереве репо.
UPLOAD_DIR="$(sudo -n awk 'BEGIN{FS="="} $1=="AD_UPLOAD_DIR"{sub(/^AD_UPLOAD_DIR=/, ""); print; exit}' "$ENV_FILE" || true)"
UPLOAD_DIR="${UPLOAD_DIR:-/home/valstan/SETKA/web/uploads/advertiser}"
if [[ -d "$UPLOAD_DIR" ]] && [[ -n "$(find "$UPLOAD_DIR" -type f -print -quit 2>/dev/null)" ]]; then
  echo "🖼  Archiving client photos from $UPLOAD_DIR..."
  tar -czf "$BACKUP_DIR/ad_uploads_$DATE.tar.gz" -C "$UPLOAD_DIR" .
  ls -t ad_uploads_*.tar.gz 2>/dev/null | tail -n +8 | xargs -r rm
  echo "✅ Photos archived: $(du -h "$BACKUP_DIR/ad_uploads_$DATE.tar.gz" | cut -f1)"
else
  echo "🖼  No client photos to archive"
fi

