# Редактирование конфигурации nginx

## Проблема
Файл `/etc/nginx/conf.d/setka.conf` требует прав root для редактирования.

## Решения

### Способ 1: Использовать скрипт редактирования
```bash
/home/valstan/SETKA/scripts/edit_nginx_config.sh
```

### Способ 2: Редактировать через sudo в терминале
```bash
sudo nano /etc/nginx/conf.d/setka.conf
# или
sudo vi /etc/nginx/conf.d/setka.conf
```

### Способ 3: Редактировать локально и копировать
1. Откройте файл в Cursor (он будет доступен только для чтения)
2. Скопируйте содержимое
3. Создайте временный файл: `/tmp/setka.conf`
4. Отредактируйте его
5. Скопируйте обратно:
```bash
sudo cp /tmp/setka.conf /etc/nginx/conf.d/setka.conf
sudo nginx -t
sudo systemctl reload nginx
```

### Способ 4: Использовать VS Code Remote с sudo
В Cursor:
1. `Ctrl+Shift+P` → `Remote-SSH: Open Configuration File`
2. Добавьте настройку для автоматического использования sudo

Или создайте симлинк в домашней директории:
```bash
sudo ln -s /etc/nginx/conf.d/setka.conf ~/setka.conf
# Редактируйте ~/setka.conf, затем:
sudo cp ~/setka.conf /etc/nginx/conf.d/setka.conf
```

## После редактирования

Всегда проверяйте конфигурацию перед перезагрузкой:
```bash
sudo nginx -t
```

Если тест успешен, перезагрузите nginx:
```bash
sudo systemctl reload nginx
```

## Бэкапы

Скрипт автоматически создает бэкап перед редактированием.
Бэкапы находятся в: `/etc/nginx/conf.d/setka.conf.backup.*`

