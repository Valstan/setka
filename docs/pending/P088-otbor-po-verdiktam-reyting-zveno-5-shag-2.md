# ~~📈 Отбор по вердиктам + рейтинг — звено 5, шаг 2 (заказ владельца 2026-08-20)~~ ЗАКРЫТО 2026-08-28 — релиз выкачен, гейт включён 20.08

> Запись `P088` реестра [PENDING_FOLLOWUPS](../PENDING_FOLLOWUPS.md). Индекс грепается, запись читается целиком, реестр целиком не читается никогда ([D-097](../../AGENTS.md)).

`⏱ 2026-08-20 · snooze 0 · ✅ ЗАКРЫТО 2026-08-28 по ре-триажу — тег «ждёт релиза и включения гейта» протух: на проде в /etc/setka/setka.env стоит CLASSIFIER_SELECTION_ENABLED=1 (рядом CLASSIFIER_PREPUBLISH_ENABLED=1, CLASSIFIER_ENFORCE_ENABLED=1, CLASSIFIER_PENDING_MAX=200), apply_wave_selection врезан в обе волны — tasks/parsing_scheduler_tasks.py:341 и modules/cascaded_bulletin.py:487 (реализация modules/classifier/selection.py:280); RATING_VIEWS_ALPHA в env не выставлена, работает дефолт 0.25 из config/classifier.py:167`

**`alpha` выбрана — 0.25** (исследование на 1078 измеренных кандидатах 28 районов: при 0.5 в
топ-5 попадает 9% постов с <100 просмотров — статистический шум; при 0 крупнейшая группа района
забирает 29% топа; 0.25 — шум 2%, монополия 24%, медиана охвата топа почти как у чистого
вовлечения). Совпала с дефолтом кода — env не выставляется. Блокер шага 2 снят.

**Построено (ветка `feat/classifier-selection-step2`, спека
`docs/superpowers/specs/2026-08-20-selection-by-rating-step2-design.md`):**

- сортировка сводки: `BulletinBuilder._sort_by_popularity` переведена с жёсткой `post_popularity`
  (=alpha 0.5) на `post_rating(alpha из конфига)`; пост без `views` → хвост очереди, не верхушка
  (раньше отсутствующие просмотры схлопывали делитель в 1). Гейта нет — откат `RATING_VIEWS_ALPHA=0.5`
  в env без деплоя;
- `selection.apply_wave_selection` — единственная точка входа отбора для волны: гейт
  `CLASSIFIER_SELECTION_ENABLED` (дефолт **выкл**, паттерн prepublish) → `fetch_publish_lips` →
  `decide_mode` (политика деградации владельца: пропуск волны → алгоритмический fallback на второй
  подряд) → Telegram-алёрт при молчании фильтра. Fail-open на внутренних отказах;
- врезано в обе волны ПОСЛЕ prepublish (тот записывает вердикты, отбор их читает): районная
  `parse_and_publish_theme` (счётчик `posts_filtered_selection`) и каскадная `cascaded_bulletin`
  (`filtered_posts_selection`); skip-wave возвращает success с внятным message.

**Итог:** ~~релиз~~ ✅ → ~~включение гейта~~ ✅ (`CLASSIFIER_SELECTION_ENABLED=1` на проде
с 2026-08-20 12:30, подтверждено в `/etc/setka/setka.env` 2026-08-28). Шаг 3 отдельным открытым
пунктом здесь не висит — он целиком в секции «🛑 Готовность к шагу 3 (снятие редакционных
фильтров) — разбор 2026-08-21: НЕ ВХОДИТЬ» выше, читать её ДО любых правок фильтров.
