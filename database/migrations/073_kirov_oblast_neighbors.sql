-- 073_kirov_oblast_neighbors.sql
-- Заполнить regions.neighbors для районов Кировской области.
--
-- Зачем: у 23 из 24 «скелетных» районов поле пустое — при активации они не
-- попадут в соседский обмен (share_neighbor_news / run_neighbor_bulletin).
-- Активация района сейчас требует помнить про neighbors отдельным шагом;
-- после этой миграции она сводится к vk_group_id + is_active.
--
-- Безопасность: источники соседей резолвятся с фильтром
-- `is_active = TRUE AND vk_group_id IS NOT NULL` (modules/cascaded_bulletin.py:199-204),
-- поэтому для ДЕЙСТВУЮЩИХ районов эта миграция — no-op до тех пор, пока
-- соответствующий сосед не будет активирован. Контент сегодняшних сводок не меняется.
--
-- Связи симметричны по построению (генератор разворачивает каждое ребро в обе
-- стороны); существующие значения не затираются, а дополняются.
--
-- ⚠️ ТРЕБУЕТ ПРОВЕРКИ ВЛАДЕЛЬЦЕМ: карта соседства составлена по географии
-- Кировской области ассистентом. Владелец знает область лично — прочитать список
-- глазами до применения. Ошибка не критична (лишний сосед = чуть больше чужих
-- новостей с гейтом по хэштегу), но проверить дешевле, чем ловить потом.
--
-- Идемпотентна: повторный прогон переписывает те же значения.

BEGIN;

UPDATE regions SET neighbors = 'omutninsk,verhnekame' WHERE code = 'afanasyevo';
UPDATE regions SET neighbors = 'kotelnich,pizhanka,sovetsk' WHERE code = 'arbazh';
UPDATE regions SET neighbors = 'falenki,nagorsk,omutninsk,slobodskoy,zuevka' WHERE code = 'belholunitsa';
UPDATE regions SET neighbors = 'falenki,nema,suna,uni,zuevka' WHERE code = 'bogorodskoe';
UPDATE regions SET neighbors = 'kumyony,slobodskoy,yurya,zuevka' WHERE code = 'chepetsk';
UPDATE regions SET neighbors = 'kotelnich,murashi,oparino,orlov,shabalino,svecha,yurya' WHERE code = 'darovskoy';
UPDATE regions SET neighbors = 'belholunitsa,bogorodskoe,omutninsk,uni,zuevka' WHERE code = 'falenki';
UPDATE regions SET neighbors = 'bal,kukmor,mi,nema,nolinsk,uni,ur,verhoshizhem,vp' WHERE code = 'klz';
UPDATE regions SET neighbors = 'arbazh,darovskoy,orichi,orlov,shabalino,svecha,tuzha,verhoshizhem' WHERE code = 'kotelnich';
UPDATE regions SET neighbors = 'chepetsk,orichi,slobodskoy,suna,verhoshizhem,zuevka' WHERE code = 'kumyony';
UPDATE regions SET neighbors = 'murashi,oparino,podosinovets' WHERE code = 'luza';
UPDATE regions SET neighbors = 'darovskoy,luza,nagorsk,oparino,yurya' WHERE code = 'murashi';
UPDATE regions SET neighbors = 'belholunitsa,murashi,slobodskoy,verhnekame,yurya' WHERE code = 'nagorsk';
UPDATE regions SET neighbors = 'bogorodskoe,klz,leb,mi,nolinsk,suna,uni,ur,verhoshizhem' WHERE code = 'nema';
UPDATE regions SET neighbors = 'klz,leb,mi,nema,sovetsk,suna,ur,verhoshizhem' WHERE code = 'nolinsk';
UPDATE regions SET neighbors = 'afanasyevo,belholunitsa,falenki,uni,verhnekame' WHERE code = 'omutninsk';
UPDATE regions SET neighbors = 'darovskoy,luza,murashi,podosinovets' WHERE code = 'oparino';
UPDATE regions SET neighbors = 'kotelnich,kumyony,orlov,slobodskoy,verhoshizhem,yurya' WHERE code = 'orichi';
UPDATE regions SET neighbors = 'darovskoy,kotelnich,orichi,yurya' WHERE code = 'orlov';
UPDATE regions SET neighbors = 'luza,oparino' WHERE code = 'podosinovets';
UPDATE regions SET neighbors = 'darovskoy,kiknur,kotelnich,svecha,tuzha' WHERE code = 'shabalino';
UPDATE regions SET neighbors = 'belholunitsa,chepetsk,kumyony,nagorsk,orichi,yurya,zuevka' WHERE code = 'slobodskoy';
UPDATE regions SET neighbors = 'bogorodskoe,kumyony,nema,nolinsk,verhoshizhem,zuevka' WHERE code = 'suna';
UPDATE regions SET neighbors = 'darovskoy,kotelnich,shabalino' WHERE code = 'svecha';
UPDATE regions SET neighbors = 'kiknur,kotelnich,shabalino,yaransk' WHERE code = 'tuzha';
UPDATE regions SET neighbors = 'bogorodskoe,falenki,klz,nema,omutninsk' WHERE code = 'uni';
UPDATE regions SET neighbors = 'afanasyevo,nagorsk,omutninsk' WHERE code = 'verhnekame';
UPDATE regions SET neighbors = 'klz,kotelnich,kumyony,leb,mi,nema,nolinsk,orichi,sovetsk,suna,ur' WHERE code = 'verhoshizhem';
UPDATE regions SET neighbors = 'chepetsk,darovskoy,murashi,nagorsk,orichi,orlov,slobodskoy' WHERE code = 'yurya';
UPDATE regions SET neighbors = 'belholunitsa,bogorodskoe,chepetsk,falenki,kumyony,slobodskoy,suna' WHERE code = 'zuevka';

COMMIT;

-- Проверка после применения:
--   SELECT code, neighbors FROM regions WHERE kind='raion' ORDER BY code;
-- Симметричность:
--   SELECT a.code, b.code FROM regions a JOIN regions b
--     ON position(b.code in a.neighbors) > 0
--   WHERE position(a.code in coalesce(b.neighbors,'')) = 0;
--   (пустая выдача = все связи двусторонние)
