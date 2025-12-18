#!/bin/bash
# Скрипт для редактирования конфигурации nginx с правами root

CONFIG_FILE="/etc/nginx/conf.d/setka.conf"
BACKUP_FILE="/etc/nginx/conf.d/setka.conf.backup.$(date +%Y%m%d_%H%M%S)"

echo "Редактирование конфигурации nginx: $CONFIG_FILE"
echo ""

# Создать бэкап
echo "Создаю бэкап: $BACKUP_FILE"
sudo cp "$CONFIG_FILE" "$BACKUP_FILE"
echo "Бэкап создан: $BACKUP_FILE"
echo ""

# Открыть файл в редакторе
echo "Открываю файл для редактирования..."
echo "После сохранения запустите: sudo nginx -t && sudo systemctl reload nginx"
echo ""

# Использовать nano или vi
if command -v nano &> /dev/null; then
    sudo nano "$CONFIG_FILE"
elif command -v vi &> /dev/null; then
    sudo vi "$CONFIG_FILE"
else
    echo "Редактор не найден. Используйте: sudo nano $CONFIG_FILE"
fi

