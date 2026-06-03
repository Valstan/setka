-- 024: message_templates.region_id — per-region шаблоны ответов.
--
-- Контекст. Шаблоны ответов (`message_templates`, этап 4b) исторически были
-- общими на все регионы (модератор один). Документация модели прямо
-- предусматривала будущий per-region: «если понадобится — добавим region_id
-- nullable + UI-фильтр». Понадобилось: некоторые ответы специфичны для района
-- (адрес/телефон редакции, локальные реалии).
--
-- Поле:
--   * region_id INTEGER NULL → regions(id) ON DELETE SET NULL — регион, к
--     которому привязан шаблон. NULL = общий (виден во всех регионах). При
--     удалении региона шаблон не удаляется, а становится общим (SET NULL).
--
-- Семантика выборки (см. ``web/api/templates.list_templates``): dropdown ответа
-- для региона X показывает шаблоны ``region_id IS NULL OR region_id = X`` —
-- общие + специфичные для X. Страница управления показывает все.
--
-- Идемпотентна: ``ADD COLUMN IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS``.

ALTER TABLE message_templates
    ADD COLUMN IF NOT EXISTS region_id INTEGER NULL REFERENCES regions(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_message_templates_region ON message_templates(region_id);
