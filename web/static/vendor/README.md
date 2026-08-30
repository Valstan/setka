# Вендоренные фронтенд-библиотеки

Здесь лежат сторонние CSS/JS, которые раньше грузились с публичных CDN
(`cdn.jsdelivr.net`, `cdnjs.cloudflare.com`) **браузером оператора**.

## Зачем это здесь

Пока библиотеки жили на CDN, вёрстка операторской панели зависела от того,
доходит ли браузер конкретного человека до чужого сервера в момент открытия
страницы. Любой сбой на этом участке — блокировка, сбойный DNS, VPN, секундная
недоступность CDN — оставлял панель голым HTML: «поехало всё сразу».

Отказ этого класса особенно неприятен тем, что **не виден со стороны сервера**:
прод здоров, health 200, в логах пусто, деплоя не было, а пользователь видит
разъехавшуюся страницу. Диагностировать его постфактум почти невозможно —
к моменту проверки он обычно уже вылечился сам.

Локальная копия убирает весь класс целиком: файлы отдаёт тот же сервер, что и
саму страницу, — если он недоступен, то недоступна и страница, и расхождения
«страница есть, стилей нет» больше не существует.

## Что лежит и откуда взято

Файлы скачаны как есть, без правок. Проверить целостность: `sha256sum`.

| Файл | Версия | Источник |
|---|---|---|
| `bootstrap/bootstrap.min.css` | 5.3.2 | `https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css` |
| `bootstrap/bootstrap.bundle.min.js` | 5.3.2 | `https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js` |
| `bootstrap-icons/bootstrap-icons.css` | 1.11.1 | `https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css` |
| `bootstrap-icons/fonts/bootstrap-icons.woff2` | 1.11.1 | `https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/fonts/bootstrap-icons.woff2` |
| `bootstrap-icons/fonts/bootstrap-icons.woff` | 1.11.1 | `https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/fonts/bootstrap-icons.woff` |
| `chartjs/chart.umd.min.js` | 4.4.0 | `https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js` |

```
bb6fd8cd85394cb367e8ac58e47292f2d68eb288fa12fab68e65430a5ddfce48  bootstrap-icons/bootstrap-icons.css
4d4572ef314e1b734cdd6485f913b0396d81bedf4d216a47cfde0cdf32a9316e  bootstrap-icons/fonts/bootstrap-icons.woff
bacd70afda7da1deac2bbd49b5717a4dd133bcd59c379525d705b8492f678e95  bootstrap-icons/fonts/bootstrap-icons.woff2
82f64f62bb03c1bc1824b0f9c9e05f70dba33e146818e63cdf5c306c8cf3dedd  bootstrap/bootstrap.bundle.min.js
3017df4a76db5f01c2b99b603d88b03106df13bcfe18e67b7c13c2341d3a67df  bootstrap/bootstrap.min.css
0e2326c6868072bec1592760c6729043caeea2960a2b46cee6a2192aac6abff0  chartjs/chart.umd.min.js
```

## Правила обращения

- **Файлы не редактировать.** Их ценность в том, что они побайтно равны
  апстриму и проверяются суммой выше. Нужна правка поведения — пиши её в
  `web/static/css/style.css`, он грузится после.
- `bootstrap-icons.css` зовёт шрифты относительным путём `./fonts/…` —
  подкаталог `fonts/` обязан лежать рядом с ним, иначе иконки исчезнут, а
  вёрстка останется целой (тихий отказ, заметный не сразу).
- Обновление версии — заменить файл, пересчитать `sha256sum`, поправить таблицу
  и прогнать `scripts/check_no_external_assets.py`.
- **Новые сторонние библиотеки — сюда же, а не ссылкой на CDN.** Сторож
  `scripts/check_no_external_assets.py` уронит тесты, если ссылка вернётся.

## Чего здесь сознательно нет

**Font Awesome.** Его тянула единственная страница `publisher.html` (15 иконок).
Вендорить ради неё целое семейство шрифтов (~1.4 МБ) не стали — страница
переведена на Bootstrap Icons, которые в проекте и так есть. Ближайшего аналога
не нашлось только у `fa-flask` («Test Group»): в Bootstrap Icons 1.11.1 колбы
нет, взят `bi-eyedropper` как ближайшая лабораторная посуда.
