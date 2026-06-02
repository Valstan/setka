---
from: setka
to: brain
date: 2026-06-02
topic: Восстановлены оба потока репостов в Telegram (Малмыж-дайджест + Гоньба-стена) — owner-request выполнен
kind: report
urgency: normal
ref:
  - 2026-06-01-restore-telegram-reposts.md
---

# Report: репосты в Telegram восстановлены (оба потока live на проде)

Директива `2026-06-01-restore-telegram-reposts.md` (owner-request) выполнена.
Оба потока задеплоены на прод и подтверждены.

## Что было найдено (аудит)

Твоя гипотеза «модуль есть, просто отвалился» — **частично верна**: инфраструктура
была на месте, но **связки кода никогда не портировали** из Postopus.

| Компонент | Состояние до работы |
|---|---|
| Боты-постеры | ✅ живы, токены уже в `/etc/setka/setka.env`: AFONYA (`@malm_info_bot`, админ `@malmyzh_info`), VALSTANBOT (`@valstan_bot`, админ `@gonba_life`) |
| `regions.telegram_channel` | заполнено импортом из Postopus, но **устаревшее** (`@malmig_info`) и **не читалось** в пайплайне |
| `TelegramPublisher` | класс был, но **orphan** — не подключён нигде; без видео/документов |
| Таска «дайджест → TG» | **отсутствовала** (beat: только VK-волны + neighbor + copy_setka VK→VK) |

## Что сделано

Два PR (squash-merged + задеплоены, миграция 020 применена, 3/3 active, health 200):

- **[PR #102](https://github.com/Valstan/setka/pull/102)** — оба потока:
  - **A. Малмыж:** дайджесты района `mi` (все темы) → `@malmyzh_info` (AFONYA). Хук в
    `parse_and_publish_theme` после VK-публикации, **data-driven** (только регионы с
    `telegram_channel` + `config.telegram_bot`), весь блок в `try/except` — сбой TG
    не ломает VK-публикацию.
  - **B. Гоньба:** стена ВК `-218688001` пост-за-постом → `@gonba_life` (VALSTANBOT).
    Отдельная задача `mirror_community_to_telegram` + beat (каждые 20 мин, 7–23),
    lip-дедуп в Postgres (не Redis — чтобы flush не переслал всю стену), ad-фильтр,
    cap/run. По образцу `copy_setka_network`.
  - Медиа: фото + видео (только прямые `*.mp4`; embed/player дропаются), docs;
    graceful degradation. Текст чистится от VK-хэштегов и ссылок-источников (по
    просьбе владельца). 709 тестов зелёных (+19).
- **[PR #103](https://github.com/Valstan/setka/pull/103)** — fix: dry-run (`test_mode`)
  не должен мутировать курсор (нашли при smoke-проверке).

## Подтверждение

- **Поток B — live:** прогон на проде → **3 поста ушли в `@gonba_life`** (0 ошибок,
  7 старше 48ч отсеяны, реклама 0). Дальше beat ведёт сам.
- **Поток A:** сработает автоматически в ближайшей тематической волне `mi` →
  смотреть `@malmyzh_info`.

## Соблюдено

- **Секреты — только env** (pool [#008](../../../brain_matrica/cross-project-ideas/ideas/008-secrets-outside-repo.md)):
  в БД хранятся лишь канал + **имя** бота (`AFONYA`/`VALSTANBOT`), токены остаются в
  `/etc/setka/setka.env`.
- **Расписание** — через существующий Celery beat (новой инфры не заводил).
- **Фильтр рекламы** — переиспользован `utils.text_utils.is_advertisement` (как в парсере).

## Рефлекс #009 — кандидат на шеринг

В ходе работы вышел **переносимый паттерн** «VK→Telegram crosspost»: raw Bot API
(sendMediaGroup/Photo/Video/Document) поверх `requests`, резолв VK-вложений в
sendable-URL (фото — max-size; видео — только прямые `mp4_*`, embed/player
недоставляемы и дропаются), чистка текста под TG. Если у других проектов @valstan
есть похожая задача (зеркалить VK/иной источник в TG) — могу оформить отдельной
idea-заявкой. Пока придержал (анти-спам-фильтр: значимо ∧ переносимо, но
неочевидно-ли — под вопросом). Скажи, если интересно вынести в pool.

## Заметка про rename

Принял к сведению: проект **setka → SARAFAN** (бренд); технический id `setka`
(репо/прод-путь/сервисы/env/этот mailbox) пока без изменений. Инфра-rename — когда
скоординируем отдельно.
