# Troubleshooting (частые проблемы)

## Быстрая диагностика

```bash
cd /home/valstan/SETKA
bash scripts/diagnose_connection.sh
```

Сводка статуса:

```bash
bash scripts/check-setka.sh
```

## FastAPI не отвечает

Проверьте env:
- `DATABASE_URL`
- `REDIS_URL`

Проверьте процесс и логи:

```bash
ps aux | grep uvicorn
tail -n 200 logs/app.log
curl http://localhost:8000/api/health/
```

## Проблемы с сайтом/доступом извне

Проверьте nginx:

```bash
sudo systemctl status nginx
sudo nginx -t
sudo netstat -tlnp | grep -E ":(80|443|8000)"
```

Проверьте редиректы/HTTPS:

```bash
curl -I http://3931b3fe50ab.vps.myjino.ru/api/health/
curl -I https://3931b3fe50ab.vps.myjino.ru/api/health/
```

## Redirect loop (HTTPS)

Часто причина — конфликт редиректов + HSTS в браузере.

Что делать:
- очистить cookies/cache для домена
- удалить HSTS policy в браузере
- перепроверить `return 301` блоки в nginx (см. `config/setka.conf.editable`)

Chrome/Edge:
- `chrome://net-internals/#hsts` → Delete domain security policies → домен → Delete

Проверка редиректов:

```bash
curl -I https://3931b3fe50ab.vps.myjino.ru/
curl -v --max-redirs 0 https://3931b3fe50ab.vps.myjino.ru/
```

## Cursor Simple Browser: SSL ошибка (-310)

Обычно это особенность встроенного браузера Cursor.

Варианты:
- открыть сайт во внешнем браузере
- использовать Live Preview для локальных шаблонов `web/templates/*.html`


