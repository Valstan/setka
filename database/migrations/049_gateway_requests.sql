-- Миграция 049: лог запросов к VK-шлюзу (страница статистики /gateway-stats).
--
-- Запрос владельца 2026-06-26: видеть, кто/когда/сколько пользуется «воротами»
-- в VK + сохранять сами запросы (что искали/спрашивали). Пишется best-effort
-- после исполнения (modules/gateway/usage.py); не блокирует ответ шлюза.
--
-- Аддитивно (CREATE TABLE IF NOT EXISTS), на живой код не влияет — безопасно
-- применять на проде. Откат тривиален (DROP TABLE), не обязателен.

CREATE TABLE IF NOT EXISTS gateway_requests (
    id          SERIAL PRIMARY KEY,
    created_at  TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    project     VARCHAR(64),    -- имя API-ключа (GATEWAY_KEY_<PROJECT>)
    endpoint    VARCHAR(32),    -- call | community | wall
    method      VARCHAR(64),    -- VK-метод
    params      JSONB,          -- что искали/спрашивали (параметры запроса)
    status      INTEGER,        -- HTTP-статус ответа (200/400/503)
    ok          BOOLEAN DEFAULT FALSE,  -- успешный VK-ответ
    error_code  INTEGER,        -- VK error_code, если был
    duration_ms INTEGER         -- длительность исполнения
);

CREATE INDEX IF NOT EXISTS ix_gateway_requests_created ON gateway_requests (created_at);
CREATE INDEX IF NOT EXISTS ix_gateway_requests_project_created
    ON gateway_requests (project, created_at);

COMMENT ON TABLE gateway_requests IS 'Лог запросов к VK-шлюзу /api/gateway для страницы статистики';
