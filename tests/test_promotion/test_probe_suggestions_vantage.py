"""Чьими глазами снята проба — свойство пары (аккаунт, сообщество).

Первая версия `scripts/probe_suggestions.py` выбирала «постороннего» один раз,
по первому району, и дальше считала его посторонним везде. На живом прогоне
18.09 это развалилось ровно там, где выбор и делался: у `vp` все три
доступных аккаунта оказались своими (владелец и МАМА — админы, третий —
подписчик), и скрипт отказался мерить **все 53** сообщества вместо одного.

Отсюда два свойства, которые держит этот файл:

1. `vantage_of` различает три взгляда, а не два: админ (его `can_suggest`
   равен нулю всегда и не значит ничего), подписчик (ответ слабее — настройка
   различает «от всех» и «только от подписчиков») и посторонний — единственный,
   чей ответ и есть искомый;
2. выбор идёт **по каждому сообществу**: членство — свойство пары, а не
   аккаунта.
"""

import importlib.util
import os

import pytest


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "probe_suggestions_script",
        os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "probe_suggestions.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_script()


class TestVantageOf:
    def test_outsider(self, mod):
        """Не подписан и писать не может — тот самый взгляд."""
        assert mod.vantage_of({"member_status": 0, "can_post": 0}) == "outsider"

    def test_subscriber(self, mod):
        """Подписчик: ответ есть, но он про подписчика."""
        assert mod.vantage_of({"member_status": 1, "can_post": 0}) == "subscriber"

    def test_admin_is_useless(self, mod):
        """У админа can_suggest=0 и там, где предложка работает годами."""
        assert mod.vantage_of({"member_status": 1, "can_post": 1}) is None

    def test_unknown_fields_are_not_a_vantage(self, mod):
        assert mod.vantage_of({}) is None


class TestMeasurePicksPerCommunity:
    def _tokens(self):
        return [("MAMA", "t-mama"), ("VALSTAN", "t-valstan"), ("VITA", "t-vita")]

    def test_outsider_wins_over_subscriber(self, mod, monkeypatch):
        """Подписчик берётся только когда постороннего нет вовсе."""
        answers = {
            "t-mama": {"member_status": 1, "can_post": 1},  # админ
            "t-vita": {"member_status": 1, "can_post": 0},  # подписчик
            "t-valstan": {"member_status": 0, "can_post": 0, "can_suggest": 1},  # посторонний
        }
        monkeypatch.setattr(mod, "call", lambda token, m, p: {"ok": [answers[token]]})
        monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

        item, vantage, name = mod.measure(self._tokens(), -1, None)
        assert vantage == "outsider" and name == "VALSTAN"
        assert item["can_suggest"] == 1

    def test_subscriber_is_used_when_no_outsider(self, mod, monkeypatch):
        """Случай `vp`: посторонних нет — мерим подписчиком и помечаем это."""
        answers = {
            "t-mama": {"member_status": 1, "can_post": 1},
            "t-valstan": {"member_status": 1, "can_post": 1},
            "t-vita": {"member_status": 1, "can_post": 0, "can_suggest": 1},
        }
        monkeypatch.setattr(mod, "call", lambda token, m, p: {"ok": [answers[token]]})
        monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

        item, vantage, name = mod.measure(self._tokens(), -1, None)
        assert vantage == "subscriber" and name == "VITA"
        assert item["can_suggest"] == 1

    def test_all_insiders_gives_nothing_not_a_false_no(self, mod, monkeypatch):
        """Гвоздь: «мерить нечем» ≠ «предложки нет».

        Прежняя версия в этом месте роняла весь прогон; ещё хуже было бы
        напечатать «НЕТ» — админский ноль выглядит как отсутствие предложки.
        """
        monkeypatch.setattr(
            mod, "call", lambda token, m, p: {"ok": [{"member_status": 1, "can_post": 1}]}
        )
        monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

        item, vantage, name = mod.measure(self._tokens(), -1, None)
        assert (item, vantage, name) == (None, None, None)

    def test_preferred_token_is_tried_first(self, mod, monkeypatch):
        """Экономия вызовов: кто был посторонним в прошлом районе — первый."""
        order = []

        def _call(token, method, params):
            order.append(token)
            return {"ok": [{"member_status": 0, "can_post": 0, "can_suggest": 1}]}

        monkeypatch.setattr(mod, "call", _call)
        monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

        mod.measure(self._tokens(), -1, "VITA")
        assert order == ["t-vita"]  # один вызов, а не три

    def test_transport_error_does_not_stop_the_search(self, mod, monkeypatch):
        """Отказ одного токена не отменяет остальные взгляды."""
        answers = {
            "t-mama": {"err": "transport: TimeoutError"},
            "t-valstan": {"ok": [{"member_status": 1, "can_post": 1}]},
            "t-vita": {"ok": [{"member_status": 0, "can_post": 0, "can_suggest": 0}]},
        }
        monkeypatch.setattr(mod, "call", lambda token, m, p: answers[token])
        monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

        item, vantage, name = mod.measure(self._tokens(), -1, None)
        assert vantage == "outsider" and name == "VITA" and item["can_suggest"] == 0
