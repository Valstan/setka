#!/bin/bash
# Диагностика подключения к SETKA

echo "🔍 Диагностика подключения к SETKA"
echo "=================================="
echo ""

# Проверка FastAPI
echo "1. Проверка FastAPI (localhost:8000)..."
if curl -s http://localhost:8000/api/health/ --max-time 3 > /dev/null; then
    echo "   ✅ FastAPI работает"
    curl -s http://localhost:8000/api/health/
    echo ""
else
    echo "   ❌ FastAPI не отвечает"
fi

# Проверка Nginx
echo ""
echo "2. Проверка Nginx..."
if systemctl is-active --quiet nginx; then
    echo "   ✅ Nginx активен"
else
    echo "   ❌ Nginx не активен"
fi

# Проверка портов
echo ""
echo "3. Проверка портов..."
if netstat -tlnp 2>/dev/null | grep -q ":8000"; then
    echo "   ✅ Порт 8000 открыт"
else
    echo "   ❌ Порт 8000 не открыт"
fi

if netstat -tlnp 2>/dev/null | grep -q ":80"; then
    echo "   ✅ Порт 80 открыт"
else
    echo "   ❌ Порт 80 не открыт"
fi

# Проверка внешнего доступа
echo ""
echo "4. Проверка внешнего доступа..."
DOMAIN="3931b3fe50ab.vps.myjino.ru"
if curl -s http://$DOMAIN/api/health/ --max-time 5 > /dev/null; then
    echo "   ✅ Сайт доступен через HTTP"
    curl -s http://$DOMAIN/api/health/
    echo ""
else
    echo "   ❌ Сайт недоступен через HTTP"
fi

# Проверка HTTPS
echo ""
echo "5. Проверка HTTPS..."
if curl -s -k https://$DOMAIN/api/health/ --max-time 5 > /dev/null 2>&1; then
    echo "   ✅ HTTPS работает"
else
    echo "   ⚠️  HTTPS не настроен (это нормально, если используется только HTTP)"
fi

# Проверка процессов
echo ""
echo "6. Проверка процессов..."
if pgrep -f "uvicorn main:app" > /dev/null; then
    echo "   ✅ FastAPI процесс работает"
    ps aux | grep "uvicorn main:app" | grep -v grep | head -1
else
    echo "   ❌ FastAPI процесс не найден"
fi

echo ""
echo "=================================="
echo "✅ Диагностика завершена"

