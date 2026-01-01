#!/bin/bash
# SETKA Project Status Check

echo "╔══════════════════════════════════════════════════════════╗"
echo "║          SETKA PROJECT STATUS CHECK                      ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# Check if FastAPI is running
if pgrep -f "python main.py" > /dev/null; then
    echo "✅ FastAPI: Running"
    API_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/ 2>/dev/null)
    echo "   API Response: $API_STATUS"
else
    echo "❌ FastAPI: Not running"
fi

# Check database
echo ""
DB_STATUS=$(sudo -u postgres psql -d setka -c "SELECT COUNT(*) FROM regions;" -t 2>/dev/null | tr -d ' ')
if [ ! -z "$DB_STATUS" ]; then
    echo "✅ PostgreSQL: Connected"
    echo "   Regions: $DB_STATUS"
    POSTS=$(sudo -u postgres psql -d setka -c "SELECT COUNT(*) FROM posts;" -t 2>/dev/null | tr -d ' ')
    echo "   Posts: $POSTS"
else
    echo "❌ PostgreSQL: Connection failed"
fi

# Check Redis
echo ""
if redis-cli ping > /dev/null 2>&1; then
    echo "✅ Redis: Running"
else
    echo "❌ Redis: Not running"
fi

# Check Nginx
echo ""
if systemctl is-active --quiet nginx; then
    echo "✅ Nginx: Running"
else
    echo "❌ Nginx: Not running"
fi

# Check SSL
echo ""
if [ -f "/etc/letsencrypt/live/3931b3fe50ab.vps.myjino.ru/fullchain.pem" ]; then
    echo "✅ SSL Certificate: Installed"
    EXPIRY=$(sudo certbot certificates 2>/dev/null | grep "Expiry Date" | head -1)
    echo "   $EXPIRY"
else
    echo "❌ SSL Certificate: Not found"
fi

# Check Ollama
echo ""
if systemctl is-active --quiet ollama; then
    echo "✅ Ollama: Running"
    MODELS=$(ollama list | tail -n +2 | wc -l)
    echo "   Models: $MODELS"
else
    echo "❌ Ollama: Not running"
fi

# Resources
echo ""
echo "💾 System Resources:"
DISK=$(df -h / | awk 'NR==2 {print $5}')
RAM=$(free | awk 'NR==2 {printf "%.0f%%", $3/$2 * 100}')
echo "   Disk: $DISK used"
echo "   RAM: $RAM used"

# Backups
echo ""
BACKUP_COUNT=$(ls -1 /home/valstan/SETKA/backup/*.sql.gz 2>/dev/null | wc -l)
echo "💾 Backups: $BACKUP_COUNT"

echo ""
echo "══════════════════════════════════════════════════════════"
echo "📚 Documentation: cat ~/SETKA/README.md"
echo "📡 API Docs: http://localhost:8000/docs"
echo "══════════════════════════════════════════════════════════"
