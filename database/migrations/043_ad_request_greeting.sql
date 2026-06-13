-- 043: авто-приветствие рекламодателю (улучшение отклика 2026-06-13).
-- greeting_sent_at — момент авто-ответа на новую заявку (один раз, идемпотентно).
-- NULL = ещё не приветствовали. Заполняет фоновая таска auto-greet-ad-requests
-- (modules/ad_cabinet/auto_greeting.py); включается per-community через env
-- AD_AUTO_GREETING_COMMUNITIES (allowlist) + текст AD_AUTO_GREETING_TEXT или
-- активный шаблон категории ad_greeting. Off по умолчанию (#008-стиль гейта).
--
-- Идемпотентна: ADD COLUMN IF NOT EXISTS.

ALTER TABLE ad_requests ADD COLUMN IF NOT EXISTS greeting_sent_at TIMESTAMP NULL;
