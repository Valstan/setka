#!/bin/bash
# Database backup script for SETKA project

# Configuration
BACKUP_DIR="/home/valstan/SETKA/backup"
DB_NAME="setka"
DB_USER="setka_user"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/setka_backup_$DATE.sql"

# Create backup directory if not exists
mkdir -p "$BACKUP_DIR"

# Backup database
echo "🔄 Creating database backup..."
# Read password from config file
PGPASSWORD=$(python3 -c "import sys; sys.path.insert(0, '/home/valstan/SETKA'); from config.config_secure import POSTGRES; print(POSTGRES['password'])")
pg_dump -h localhost -U $DB_USER -d $DB_NAME > "$BACKUP_FILE"

if [ $? -eq 0 ]; then
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
    
    exit 0
else
    echo "❌ Backup failed!"
    exit 1
fi

