"""Отбор по меткам + политика деградации (звено 5, D-024).

Здесь решается, что уйдёт в ленты 26 районов, поэтому тесты стерегут именно
политику владельца дословно: пропустить ОДНУ волну, на второй опубликовать
алгоритмами, пропущенную НЕ досылать, и в обоих случаях сообщить.
"""

from __future__ import annotations

from modules.classifier import selection


class FakeRedis:
    """Минимальный Redis: get/setex/delete. Ошибки — отдельными подклассами."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)


class BrokenRedis(FakeRedis):
    def get(self, key):
        raise RuntimeError("redis down")


def _decide(publish_lips, redis_client, *, region="mi", theme="novost"):
    return selection.decide_mode(
        publish_lips=publish_lips,
        region_code=region,
        theme=theme,
        redis_client=redis_client,
    )


# ───────── норма ─────────


def test_verdicts_present_means_selection_by_labels():
    mode, alert = _decide({"1_1"}, FakeRedis())
    assert mode == selection.MODE_VERDICTS
    assert alert is False


def test_successful_wave_clears_the_skip_mark():
    """Один сбой не должен вести к алгоритмам через неделю: успешная волна
    сбрасывает счётчик."""
    r = FakeRedis()
    _decide(set(), r)  # пропуск, отметка поставлена
    assert r.store
    _decide({"1_1"}, r)  # успех
    assert r.store == {}


# ───────── политика деградации ─────────


def test_first_silent_wave_is_skipped_not_published():
    mode, alert = _decide(set(), FakeRedis())
    assert mode == selection.MODE_SKIP_WAVE
    assert alert is True


def test_second_silent_wave_falls_back_to_algorithms():
    r = FakeRedis()
    assert _decide(set(), r)[0] == selection.MODE_SKIP_WAVE
    assert _decide(set(), r)[0] == selection.MODE_FALLBACK


def test_policy_repeats_and_does_not_stick_in_fallback():
    """После отката отметка снимается: следующий отказ снова начинается с
    пропуска волны, а не остаётся в режиме алгоритмов навсегда."""
    r = FakeRedis()
    _decide(set(), r)
    _decide(set(), r)  # fallback
    assert _decide(set(), r)[0] == selection.MODE_SKIP_WAVE


def test_waves_are_counted_per_theme():
    """У novost и afisha разные календари; общий счётчик пропускал бы
    публикации там, где всё в порядке."""
    r = FakeRedis()
    assert _decide(set(), r, theme="novost")[0] == selection.MODE_SKIP_WAVE
    assert _decide(set(), r, theme="afisha")[0] == selection.MODE_SKIP_WAVE


def test_waves_are_counted_per_region():
    r = FakeRedis()
    assert _decide(set(), r, region="mi")[0] == selection.MODE_SKIP_WAVE
    assert _decide(set(), r, region="ur")[0] == selection.MODE_SKIP_WAVE


def test_no_redis_skips_the_wave_never_falls_back_silently():
    """Без Redis отметку хранить негде. Молча уйти в алгоритмы значит выпустить
    в ленту то, за что банят аккаунт."""
    mode, alert = _decide(set(), None)
    assert mode == selection.MODE_SKIP_WAVE
    assert alert is True


def test_broken_redis_degrades_to_skip_not_to_fallback():
    mode, _ = _decide(set(), BrokenRedis())
    assert mode == selection.MODE_SKIP_WAVE


# ───────── алёрт ─────────


def test_alert_text_names_region_theme_and_what_to_check():
    text = selection.format_alert(mode=selection.MODE_SKIP_WAVE, region_code="mi", theme="novost")
    assert "mi" in text and "novost" in text
    assert "DeepSeek" in text
    assert "не опубликована" in text


def test_fallback_alert_warns_about_spam():
    text = selection.format_alert(mode=selection.MODE_FALLBACK, region_code="mi", theme="novost")
    assert "алгоритм" in text.lower()
    assert "спам" in text.lower()
    assert "НЕ досылается" in text


def test_alert_needs_telegram_config():
    status = selection.maybe_alert(
        mode=selection.MODE_SKIP_WAVE,
        region_code="mi",
        theme="novost",
        telegram_token="",
        chat_id="",
    )
    assert status == "skipped:no-telegram-config"


def test_alert_cooldown_is_per_region_not_per_theme(monkeypatch):
    """Фильтр встаёт сразу для всех тем; письмо на каждую волну каждой темы —
    способ научить владельца не читать эти алёрты."""
    import sys
    from types import SimpleNamespace

    r = FakeRedis()
    sent = []

    class Resp:
        status_code = 200

    def fake_post(*_a, **kw):
        sent.append(kw)
        return Resp()

    # Модуль импортирует requests внутри функции — подменяем сам пакет.
    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(post=fake_post))

    first = selection.maybe_alert(
        mode=selection.MODE_SKIP_WAVE,
        region_code="mi",
        theme="novost",
        telegram_token="t",
        chat_id="42",
        redis_client=r,
    )
    second = selection.maybe_alert(
        mode=selection.MODE_SKIP_WAVE,
        region_code="mi",
        theme="afisha",
        telegram_token="t",
        chat_id="42",
        redis_client=r,
    )
    assert first == "alert-sent"
    assert second == "skipped:cooldown"
    assert len(sent) == 1
