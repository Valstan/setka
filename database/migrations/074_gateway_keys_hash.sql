-- 074: gateway_keys — хэш секрета вместо открытого значения, шаг 1 из 3
-- (мандат brain 2026-08-01, 🔴 «GatewayKey.secret хранится и сравнивается в открытом виде»).
--
-- Зачем: компрометация дампа БД сегодня отдаёт все ключи потребителей шлюза
-- открытым текстом. У OIDC-клиентов хэш есть (client_secret_hash), у ключей
-- шлюза не было. Лечится тем же приёмом: хэш + префикс для опознания.
--
-- Почему sha256, а не scrypt как у паролей: ключ — secrets.token_urlsafe(32),
-- 256 бит машинной энтропии (перебирать нечего), а проверка идёт на КАЖДОМ
-- запросе потребителя. Медленный KDF здесь не добавил бы стойкости, но стоил
-- бы задержки на всём трафике шлюза. Разбор — в docstring modules/gateway/keys.py.
--
-- ⚠️ ЭТА МИГРАЦИЯ ОБРАТНОСОВМЕСТИМА и применяется ДО рестарта нового кода:
-- только добавляет колонки и заполняет их из уже имеющихся значений. Старый
-- код колонок не видит и продолжает работать на plaintext. Зануление самого
-- секрета — отдельная миграция 075, ПОСЛЕ рестарта: иначе между применением
-- и рестартом шлюз отвечал бы потребителям 401.
--
-- Потребителей ре-выдача НЕ затрагивает: их ключи не меняются, мы лишь
-- перестаём хранить значение у себя.

ALTER TABLE gateway_keys ADD COLUMN IF NOT EXISTS secret_sha256 VARCHAR(64);
ALTER TABLE gateway_keys ADD COLUMN IF NOT EXISTS secret_prefix VARCHAR(16);

-- Backfill из plaintext: pgcrypto может отсутствовать, поэтому считаем
-- встроенным digest'ом sha256 над bytea — он есть в ядре с PG 11.
UPDATE gateway_keys
   SET secret_sha256 = encode(sha256(secret::bytea), 'hex'),
       secret_prefix = left(secret, 6)
 WHERE secret IS NOT NULL
   AND secret_sha256 IS NULL;

CREATE INDEX IF NOT EXISTS ix_gateway_keys_secret_sha256 ON gateway_keys (secret_sha256);

-- Проверка после применения (должно быть 0 строк):
--   SELECT name FROM gateway_keys WHERE secret IS NOT NULL AND secret_sha256 IS NULL;
--
-- Откат:
--   DROP INDEX IF EXISTS ix_gateway_keys_secret_sha256;
--   ALTER TABLE gateway_keys DROP COLUMN IF EXISTS secret_sha256;
--   ALTER TABLE gateway_keys DROP COLUMN IF EXISTS secret_prefix;
