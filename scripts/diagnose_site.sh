#!/bin/bash
# Диагностика доступности сайта SETKA

echo "=== Диагностика сайта SETKA ==="
echo ""

echo "1. Проверка процессов:"
ps aux | grep -E "(uvicorn|nginx)" | grep -v grep
echo ""

echo "2. Проверка портов:"
ss -tlnp | grep -E "(80|443|8000)"
echo ""

echo "3. Проверка локального доступа к приложению:"
curl -s -o /dev/null -w "HTTP: %{http_code}, Time: %{time_total}s\n" http://127.0.0.1:8000/api/health/
echo ""

echo "4. Проверка доступа через nginx (локально):"
curl -s -o /dev/null -w "HTTP: %{http_code}, Time: %{time_total}s\n" https://3931b3fe50ab.vps.myjino.ru/api/health/
echo ""

echo "5. Проверка корневой страницы:"
curl -s -o /dev/null -w "HTTP: %{http_code}, Time: %{time_total}s, Size: %{size_download} bytes\n" https://3931b3fe50ab.vps.myjino.ru/
echo ""

echo "6. Проверка статических файлов:"
curl -s -o /dev/null -w "CSS: HTTP %{http_code}\n" https://3931b3fe50ab.vps.myjino.ru/static/css/style.css
curl -s -o /dev/null -w "JS: HTTP %{http_code}\n" https://3931b3fe50ab.vps.myjino.ru/static/js/main.js
echo ""

echo "7. Последние ошибки nginx:"
sudo tail -5 /home/valstan/SETKA/logs/nginx_error.log 2>/dev/null || echo "Нет ошибок"
echo ""

echo "8. Статус nginx:"
sudo systemctl is-active nginx
echo ""

echo "9. Проверка SSL сертификата:"
sudo certbot certificates 2>&1 | grep -A 5 "3931b3fe50ab" | head -10
echo ""

echo "=== Диагностика завершена ==="

