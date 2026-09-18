# 🌍 Big idea — модуль авто-регистрации регионов и сообществ

> Запись `P044` реестра [PENDING_FOLLOWUPS](../PENDING_FOLLOWUPS.md). Индекс грепается, запись читается целиком, реестр целиком не читается никогда ([D-097](../../AGENTS.md)).

**MVP + recheck закрыты 2026-05-22** (см. `DEV_HISTORY.md`):
- ✅ Миграция 011: `community_candidates` + `regions.vk_city_id/center_city` + `communities.health_*` + composite индекс (region_id, vk_id).
- ✅ Backend MVP: `modules/discovery/{vk_search,ai_categorizer}.py`, `tasks/discovery_tasks.py`.
- ✅ Web API: `/api/discovery/{cities,trigger,candidates,candidates/{id},candidates/bulk}`.
- ✅ UI: `/regions/new` wizard с auto-resolve VK city, `/regions/<code>/discovery` с фильтрами / approve modal / bulk-actions.
- ✅ **Recheck (итерация 2)**: `modules/discovery/health_check.py` + `tasks.discovery_tasks.recheck_communities_for_region` / `recheck_all_active_regions`. Beat entry `discovery-recheck-weekly` (Mon 04:00 MSK). Telegram-alert по итогам. VK errors 15/18/100/203 → `dead`; пост старше `dormant_days` (default 60, override `region.config['dormant_days']`) → `dormant`; AI drift при confidence ≥ 70 → `changed_category` + `suggested_category`.
- ✅ +90 тестов всего по big idea (60 MVP + 30 recheck), 360/360 зелёных.
