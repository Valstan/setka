"""Ссылки на соседей в визитке — красивым адресом, не ``club<id>``.

Закон о ссылках (``AGENTS.md``): внутрь системы — числовой ``owner_id``, наружу
— короткий адрес ``vk.com/<screen_name>``. Публикация и парсинг адресуются
числом и от переименования группы не зависят; человек же читает ссылку.

Здесь чинится место, где закон нарушался ровно там, где его видно: визитка во
всех 41 сообществе ссылалась на соседей как на ``vk.com/club241197723``, потому
что ``neighbor_index`` звал ``community_url(id, None)`` — второй аргумент был
захардкожен в ``None``, хотя красивый адрес лежал закэшированным в
``Region.config['screen_name']``.

Фолбэк обязан сохраниться: ``club<id>`` работает всегда и не зависит от того,
добежала ли ночная таска кэширования.
"""

import importlib.util
import os

import pytest


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "setup_groups_script_links",
        os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "setup_groups.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_script()


def _target(code, name, gid, screen_name=None):
    return {
        "code": code,
        "name": name,
        "vk_group_id": gid,
        "screen_name": screen_name,
        "region_id": 1,
        "neighbors": None,
        "zagolovki": None,
    }


def test_pretty_address_is_used_when_known(mod):
    index = mod.neighbor_index([_target("orichi", "ОРИЧИ - ИНФО", -241197723, "orichi_info43")])
    assert index["orichi"]["url"] == "https://vk.com/orichi_info43"


def test_falls_back_to_club_id_when_unknown(mod):
    """Кэш мог не добежать — ссылка обязана остаться рабочей."""
    index = mod.neighbor_index([_target("orichi", "ОРИЧИ - ИНФО", -241197723)])
    assert index["orichi"]["url"] == "https://vk.com/club241197723"


def test_negative_owner_id_does_not_leak_into_the_url(mod):
    """``vk.com/club-241197723`` — битая ссылка; знак снимается."""
    index = mod.neighbor_index([_target("x", "X - ИНФО", -241197723)])
    assert "club-" not in index["x"]["url"]
