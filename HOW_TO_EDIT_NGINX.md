# Как редактировать конфигурацию nginx в Cursor

## Проблема
Файл `/etc/nginx/conf.d/setka.conf` требует прав root, поэтому Cursor не может его сохранить напрямую.

## Решение: Редактируемая копия

### Шаг 1: Редактируйте файл
Откройте и отредактируйте файл:
```
/home/valstan/SETKA/config/setka.conf.editable
```

Это копия конфигурации, которую вы можете редактировать без прав root.

### Шаг 2: Примените изменения
После редактирования запустите скрипт:
```bash
/home/valstan/SETKA/scripts/apply_nginx_config.sh
```

Скрипт:
1. Создаст бэкап текущей конфигурации
2. Скопирует ваши изменения в `/etc/nginx/conf.d/setka.conf`
3. Проверит синтаксис
4. Предложит перезагрузить nginx

### Альтернативный способ: Через терминал
```bash
# Редактировать через nano
sudo nano /etc/nginx/conf.d/setka.conf

# Или через vi
sudo vi /etc/nginx/conf.d/setka.conf

# После редактирования проверить
sudo nginx -t

# Применить изменения
sudo systemctl reload nginx
```

## Быстрая команда

Создайте алиас в `~/.bashrc`:
```bash
alias edit-nginx='nano /home/valstan/SETKA/config/setka.conf.editable && /home/valstan/SETKA/scripts/apply_nginx_config.sh'
```

Тогда можно просто:
```bash
edit-nginx
```

## Важно

- Всегда проверяйте синтаксис перед применением: `sudo nginx -t`
- Бэкапы создаются автоматически в `/etc/nginx/conf.d/setka.conf.backup.*`
- Если что-то пошло не так, скрипт автоматически восстановит из бэкапа

