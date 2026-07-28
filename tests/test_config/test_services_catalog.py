"""Инварианты каталога сервисов экосистемы (``config/services_catalog.py``).

БД не нужна — каталог статический. Тесты держат ровно те свойства, поломка
которых уже случалась или заметна только на живой странице:

- **домен экосистемы.** Карточка Сабантуя полгода вела на ``сабантуймалмыж.рф``
  — домен, съехавший на ``сабантуй.вмалмыже.рф`` ещё 28.06 (замечено самим
  Сабантуем 2026-07-28, он прочитал этот файл напрямую). Живьём ссылку никто
  не открывал, потому что 301-редирект со старого домена маскировал ошибку.
  Правило «сервис экосистемы живёт под ``вмалмыже.рф``» ловит это на pytest.
- **обязательные поля.** Шаблон ``services.html`` разыменовывает все четыре
  ключа; пропуск отрисуется пустотой, а не упадёт.
- **``get_services`` отдаёт копию** — страница не должна мутировать конфиг.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from config.services_catalog import SERVICES, get_services

REQUIRED_KEYS = {"title", "emoji", "url", "desc"}

# Домен экосистемы. Сервис вне него — осознанное исключение: добавляя такой,
# правь этот список вместе с карточкой, чтобы решение осталось видимым.
ECOSYSTEM_DOMAIN = "вмалмыже.рф"


class TestCatalogEntries:
    def test_catalog_is_not_empty(self):
        assert SERVICES, "каталог не может быть пустым — страница /services осиротеет"

    def test_every_entry_has_required_keys_non_empty(self):
        for entry in SERVICES:
            missing = REQUIRED_KEYS - set(entry)
            assert not missing, f"{entry.get('title', entry)}: нет ключей {missing}"
            for key in REQUIRED_KEYS:
                assert entry[key].strip(), f"{entry.get('title', entry)}: пустой {key}"

    def test_urls_are_https(self):
        for entry in SERVICES:
            assert urlsplit(entry["url"]).scheme == "https", entry["title"]

    def test_urls_live_under_ecosystem_domain(self):
        """Регрессия на съехавший домен Сабантуя (см. docstring модуля)."""
        for entry in SERVICES:
            host = urlsplit(entry["url"]).hostname or ""
            assert host.endswith(ECOSYSTEM_DOMAIN), (
                f"{entry['title']}: {host} вне {ECOSYSTEM_DOMAIN} — "
                "либо домен устарел, либо исключение нужно внести в тест осознанно"
            )

    def test_urls_have_no_trailing_slash(self):
        """Единый стиль: ссылки в каталоге — голый origin, без хвостового «/»."""
        for entry in SERVICES:
            assert not entry["url"].endswith("/"), entry["title"]

    def test_urls_are_unique(self):
        urls = [e["url"] for e in SERVICES]
        assert len(urls) == len(set(urls)), "две карточки ведут на один адрес"

    def test_titles_are_unique(self):
        titles = [e["title"] for e in SERVICES]
        assert len(titles) == len(set(titles))


class TestGetServices:
    def test_returns_all_entries(self):
        assert len(get_services()) == len(SERVICES)

    def test_returns_copies_not_the_config_itself(self):
        """Страница мутирует свой список — конфиг процесса должен уцелеть."""
        first = get_services()
        first[0]["title"] = "ИСПОРЧЕНО"
        assert get_services()[0]["title"] != "ИСПОРЧЕНО"
        assert SERVICES[0]["title"] != "ИСПОРЧЕНО"
