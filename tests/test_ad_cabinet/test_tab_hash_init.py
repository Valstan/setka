"""Проводка вкладок единого кабинета `/ad` (жалоба владельца 2026-09-06).

Владелец открыл `/ad#scheduler` — вкладка показалась, но селект «Сообщество»
навсегда остался в «— загрузка… —»: `initScheduler()` не вызывался.

Корень — порядок в inline-скрипте `ad.html`: вкладка активировалась по hash
**до** того, как навешен слушатель `shown.bs.tab`. Bootstrap 5.3 отдаёт это
событие синхронно внутри `.show()`, когда панель ещё не видна и анимировать
нечего, поэтому показ уходил в пустоту, а ленивый загрузчик списка сообществ
не стартовал никогда.

Проверки статические (JS-раннера в проекте нет), но ловят ровно те два
инварианта, поломка которых воспроизводит жалобу, плюс мёртвую проводку на
`shown.bs.collapse` — «гейт без области» (#284): `#scheduler-body` в едином
кабинете `tab-pane`, а не `collapse`, и такое событие прийти не может.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
AD_HTML = ROOT / "web" / "templates" / "ad.html"
AD_CABINET_JS = ROOT / "web" / "static" / "js" / "ad_cabinet.js"


@pytest.fixture(scope="module")
def ad_html() -> str:
    return AD_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ad_cabinet_js() -> str:
    return AD_CABINET_JS.read_text(encoding="utf-8")


class TestHashTabInit:
    def test_scheduler_hash_maps_to_its_tab(self, ad_html):
        assert "'#scheduler': 'tab-scheduler-btn'" in ad_html

    def test_lazy_listeners_registered_before_hash_activation(self, ad_html):
        """Слушатель показа вкладки — строго ДО `.show()` по hash.

        Обратный порядок и есть жалоба: `.show()` синхронно проглатывает
        `shown.bs.tab`, и ленивый инициализатор не запускается.
        """
        listener = ad_html.index("addEventListener('shown.bs.tab'")
        activation = ad_html.index("adShowTabByHash();")
        assert listener < activation, (
            "ленивые инициализаторы вкладок должны навешиваться до активации "
            "вкладки по hash — иначе событие показа уходит в пустоту"
        )

    def test_hash_activation_also_calls_init_directly(self, ad_html):
        """Страховка от синхронного события: инициализатор зовётся напрямую."""
        block = ad_html[ad_html.index("function adShowTabByHash()") :]
        block = block[: block.index("\n}")]
        assert "Tab.getOrCreateInstance(btn).show()" in block
        assert "AD_TAB_INIT[btnId]()" in block

    def test_scheduler_has_a_lazy_initializer(self, ad_html):
        assert "'tab-scheduler-btn': () => (typeof initScheduler === 'function'" in ad_html

    def test_hashchange_switches_tab(self, ad_html):
        """Переход на #scheduler со страницы /ad не перезагружает документ."""
        assert "window.addEventListener('hashchange', adShowTabByHash)" in ad_html


class TestNoDeadCollapseGate:
    def test_scheduler_body_is_a_tab_pane_not_a_collapse(self, ad_html):
        assert '<div id="scheduler-body">' in ad_html
        assert 'id="scheduler-body" class="collapse' not in ad_html

    def test_no_collapse_listener_on_scheduler_body(self, ad_cabinet_js):
        """#284 «гейт без области»: событие, которое не может прийти."""
        assert "addEventListener('shown.bs.collapse'" not in ad_cabinet_js, (
            "#scheduler-body — tab-pane; слушатель shown.bs.collapse на нём "
            "никогда не сработает и маскирует настоящую проводку"
        )
