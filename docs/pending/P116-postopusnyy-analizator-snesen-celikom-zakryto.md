# ~~🧹 `PostAnalyzer` / `RealWorkflow` / `/api/test-workflow` — постопусный анализатор снесён целиком~~ ЗАКРЫТО 2026-09-04 — решение владельца «подчерпни полезное и сноси»

> Запись `P116` реестра [PENDING_FOLLOWUPS](../PENDING_FOLLOWUPS.md). Индекс грепается, запись читается целиком, реестр целиком не читается никогда ([D-097](../../AGENTS.md)).

`⏱ 2026-09-04 · snooze 0 · ✅ ЗАКРЫТО 2026-09-04`

Хвост `/deadcode` #621: после снятия `analyze_post` класс `PostAnalyzer` (keyword-категории +
чёрный список + `SentimentAnalyzer`, ветка Groq вырезана в D-024) остался без единого вызова.
Держал его только `RealWorkflowManager` (`modules/real_workflow.py`) — ранний «демонстрационный
пайплайн» из Постопуса: сканирует 5 сообществ, «публикует» в VK/TG/OK/сайт **только логом**, а
за ним роутер `/api/test-workflow` и карточка «🎛️ Ручное управление» на `/monitoring`.
**Карточка врала:** обещала «реальный workflow региона, как beat-волна», а запускала этот муляж;
на проде за весь срок хранения логов uvicorn/nginx к `/api/test-workflow` ни одного обращения.

**Что взято перед сносом — ничего нового, всё уже живёт в боевом коде:**
- «просмотры — главный сигнал» (постопусная формула 50 engagement / 30 relevance / 20 recency)
  → рейтинг `engagement / (views+1)^alpha`, `RATING_VIEWS_ALPHA` (`config/classifier.py:194`),
  причём как *отношение*, а не абсолют — точнее исходной идеи;
- чёрный список с TTL-кэшем → `BlacklistFilter._get_blacklist` в `modules/filters/content.py`
  (там же ссылка на Postopus, 1177 слов);
- «свежесть» → окно источника `CLASSIFIER_SOURCE_DAYS=1` («жить только свежим», 2026-08-19);
- keyword-словарь категорий → заменён HITL-классификатором на DeepSeek, возвращать нечего.

Снято: `modules/ai_analyzer/analyzer.py`, `modules/real_workflow.py`, `web/api/test_workflow.py`,
две строки `main.py`, карточка + две JS-функции + загрузчик select'а в `monitoring.html`, запись
в `tests/test_main_imports.py`, упоминание в `docs/paths.md`. **Оставлены** живые соседи:
`SentimentAnalyzer` (нужен `bulletin_splitter`), `VKMonitor` (скрипты), `service_activity_notifier`
(`system_status_notifier`, `web/api/notifications.py`). Ручной прогон региона — штатно
`scripts/smoke_test.py --region <code>` (dry-run) и beat-волна.
