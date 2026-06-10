-- 036: pg_trgm для fuzzy-поиска клиентов CRM (brain pool #035, Уровень 3).
--
-- Расширение pg_trgm: триграммная similarity() для «похожих» совпадений
-- (опечатки/перестановки), когда substring-поиск дал ноль. GIN-индексы
-- ускоряют и similarity, и ILIKE '%q%' (лидирующий wildcard btree не берёт).
-- Таблица ad_clients крошечная (десятки строк), индексы — задел на рост.
--
-- Применение: ssh setka 'cd /home/valstan/SETKA && python3 scripts/migrate.py up'
-- (или вручную: sudo -u postgres psql -d setka -f database/migrations/036_pg_trgm_ad_clients.sql)

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_ad_clients_name_trgm
    ON ad_clients USING gin (name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_ad_clients_contact_trgm
    ON ad_clients USING gin (contact gin_trgm_ops);
