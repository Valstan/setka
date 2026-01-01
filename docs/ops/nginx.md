# Nginx (reverse proxy) — как редактировать и применять

## Где редактировать

Не редактируйте напрямую `/etc/nginx/conf.d/setka.conf` из Cursor.

Редактируемая копия:

- `config/setka.conf.editable`

## Как применить

Скрипт применения:

```bash
/home/valstan/SETKA/scripts/apply_nginx_config.sh
```

Что делает:
- создаёт бэкап `/etc/nginx/conf.d/setka.conf.backup.*`
- копирует `config/setka.conf.editable` → `/etc/nginx/conf.d/setka.conf`
- проверяет `nginx -t`
- **спрашивает подтверждение** на `systemctl reload nginx` (интерактивно)

Альтернатива: ручное редактирование root-файла:

```bash
/home/valstan/SETKA/scripts/edit_nginx_config.sh
```

## Что должно работать (ожидаемо)

- `:80` → редирект на `:443`
- `:443` → проксирование на `127.0.0.1:8000`
- `/static` → alias на `web/static`


