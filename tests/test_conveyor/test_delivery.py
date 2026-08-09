"""Тесты доставки на ingest сайта — выход конвейера.

Ветка безлюдная и исходящая: ошибка здесь видна не нам, а читателям сайта.
Поэтому тесты держат три вещи — что мусор не улетает, что ретраим ровно то, что
можно ретраить, и что ключ идемпотентности собран верно (иначе приёмник заведёт
дубль вместо обновления черновика).
"""

from __future__ import annotations

import pytest

from modules.conveyor import delivery
from tests.test_conveyor.conftest import SITE

GOOD_TEXT = (
    "В Малмыже отремонтировали участок дороги по улице Ленина. Работы шли две недели, "
    "подрядчик уложил новое покрытие и обновил разметку у школы."
)


def _payload(**over):
    body = {
        "vkPostId": "-158787639_77646",
        "sourceUrl": "https://vk.com/wall-158787639_77646",
        "title": "Отремонтировали улицу Ленина",
        "text": GOOD_TEXT,
    }
    body.update(over)
    return body


# ───────── ключ идемпотентности ─────────


class TestVkPostId:
    def test_adds_minus_sign_for_community_owner(self):
        """lip хранит owner по модулю, приёмник ждёт ВК-нотацию со знаком."""
        assert delivery.lip_to_vk_post_id("158787639_77646") == "-158787639_77646"

    @pytest.mark.parametrize(
        "bad", ["", "   ", "158787639", "abc_12", "158787639_", "_77646", None]
    )
    def test_malformed_lip_is_none(self, bad):
        assert delivery.lip_to_vk_post_id(bad) is None


# ───────── инвариант мусора (pool #133) ─────────


class TestInvariant:
    def test_good_payload_passes(self):
        assert delivery.check_invariant(_payload()) is None

    def test_missing_idempotency_key_blocks(self):
        assert delivery.check_invariant(_payload(vkPostId="")) == "no_vk_post_id"

    def test_missing_source_url_blocks(self):
        """Атрибуция обязательна по контракту — без неё пост уходить не должен."""
        assert delivery.check_invariant(_payload(sourceUrl="")) == "no_source_url"

    def test_empty_title_blocks(self):
        assert delivery.check_invariant(_payload(title="  ")) == "empty_title"

    @pytest.mark.parametrize(
        "text",
        [
            "Здравствуйте, {author_name}! " + GOOD_TEXT,
            "{{ name }} " + GOOD_TEXT,
            "Привет, %(user)s. " + GOOD_TEXT,
            "Значение $SECRET_TOKEN " + GOOD_TEXT,
        ],
    )
    def test_unresolved_placeholder_blocks(self, text):
        """Шаблон, уехавший неподставленным, — ровно инцидент 08.08 в другой ветке."""
        assert delivery.check_invariant(_payload(text=text)) == "unresolved_placeholder"

    def test_placeholder_in_title_blocks(self):
        assert (
            delivery.check_invariant(_payload(title="Новость {section}"))
            == "unresolved_placeholder"
        )

    def test_plain_curly_text_is_not_a_placeholder(self):
        """Фигурные скобки в живом тексте — не повод задерживать новость."""
        assert delivery.check_invariant(_payload(text=GOOD_TEXT + " Смайл {} в конце.")) is None

    def test_too_short_blocks(self):
        assert delivery.check_invariant(_payload(text="Коротко.")) == "text_too_short"

    def test_too_long_blocks(self):
        assert delivery.check_invariant(_payload(text="а" * 20001)) == "text_too_long"

    def test_low_letter_ratio_blocks(self):
        """Текст из цифр и знаков — не новость: таблица, мусор или битая кодировка."""
        assert delivery.check_invariant(_payload(text="1234567890 " * 20)) == "low_letter_ratio"

    def test_question_marks_alone_do_not_block(self):
        """Порог «?»×3 из инцидента с кодировкой сюда НЕ переносится — он про другое.

        «Что?? Уже?! Правда?» — нормальный человеческий текст, и задерживать
        новость из-за него значит наказывать за пунктуацию.
        """
        assert delivery.check_invariant(_payload(text="Что?? Уже?! Правда? " + GOOD_TEXT)) is None


# ───────── сборка тела ─────────


class TestBuildPayload:
    def test_maps_post_and_verdict(self):
        body = delivery.build_payload(
            {"lip": "1_10", "url": "https://vk.com/wall-1_10", "text": "исходный"},
            {"title": "Заголовок", "text": "отредактированный", "section": "novosti"},
            date_iso="2026-08-09T10:00:00",
        )
        assert body["vkPostId"] == "-1_10"
        assert body["text"] == "отредактированный"
        assert body["section"] == "novosti"
        assert body["date"] == "2026-08-09T10:00:00"

    def test_falls_back_to_original_text(self):
        """Редактура не вернулась — шлём исходный пост, а не пустоту."""
        body = delivery.build_payload(
            {"lip": "1_10", "url": "u", "text": "исходный"}, {"title": "З"}
        )
        assert body["text"] == "исходный"

    def test_only_photo_and_doc_urls_go_as_images(self):
        body = delivery.build_payload(
            {
                "lip": "1_10",
                "url": "u",
                "text": "t",
                "media": [
                    {"type": "photo", "url": "https://cdn/1.jpg"},
                    {"type": "video", "title": "без url"},
                    {"type": "doc", "url": "https://cdn/2.pdf"},
                    {"type": "photo"},
                ],
            },
            {"title": "З"},
        )
        assert body["images"] == ["https://cdn/1.jpg", "https://cdn/2.pdf"]

    def test_images_capped_at_receiver_limit(self):
        media = [{"type": "photo", "url": f"https://cdn/{i}.jpg"} for i in range(25)]
        body = delivery.build_payload({"lip": "1_10", "url": "u", "text": "t", "media": media}, {})
        assert len(body["images"]) == delivery.MAX_IMAGES

    def test_absent_optional_fields_are_omitted(self):
        body = delivery.build_payload({"lip": "1_10", "url": "u", "text": "t"}, {})
        assert "section" not in body and "images" not in body and "date" not in body


# ───────── доставка и ретраи ─────────


class _Caller:
    """Подменяет один HTTP-вызов заранее заданной цепочкой ответов."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0
        self.headers = None

    def __call__(self, url, key, body, *, timeout):
        self.calls += 1
        self.headers = key
        return self.results[min(self.calls - 1, len(self.results) - 1)]


@pytest.fixture
def slept():
    return []


class TestDeliver:
    def test_success_returns_remote_id(self, monkeypatch):
        caller = _Caller([(201, {"id": "doc-42"})])
        monkeypatch.setattr(delivery, "_post_once", caller)
        out = delivery.deliver(SITE, "s3cret", _payload())
        assert out["ok"] and out["status"] == 201 and out["remote_id"] == "doc-42"
        assert out["attempts"] == 1
        assert caller.headers == "s3cret"

    @pytest.mark.parametrize("code", [502, 503, 504])
    def test_retries_gateway_errors_then_succeeds(self, monkeypatch, slept, code):
        """G234: пятисотки на этом маршруте будут, и ретрай безопасен — приёмник
        идемпотентен по vkPostId."""
        caller = _Caller([(code, {}), (200, {"id": "x"})])
        monkeypatch.setattr(delivery, "_post_once", caller)
        out = delivery.deliver(SITE, "k", _payload(), sleep=slept.append)
        assert out["ok"] and out["attempts"] == 2 and slept == [2]

    def test_network_failure_is_retried(self, monkeypatch, slept):
        caller = _Caller([(0, {"error": "timeout"}), (0, {"error": "timeout"}), (200, {})])
        monkeypatch.setattr(delivery, "_post_once", caller)
        out = delivery.deliver(SITE, "k", _payload(), sleep=slept.append)
        assert out["ok"] and out["attempts"] == 3 and slept == [2, 4]

    def test_gives_up_after_attempts(self, monkeypatch, slept):
        caller = _Caller([(503, {})])
        monkeypatch.setattr(delivery, "_post_once", caller)
        out = delivery.deliver(SITE, "k", _payload(), attempts=3, sleep=slept.append)
        assert not out["ok"] and out["attempts"] == 3 and out["reason"] == "http_503"

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
    def test_client_errors_are_not_retried(self, monkeypatch, code):
        """4xx — наша ошибка в теле или ключе; повтор её не исправит."""
        caller = _Caller([(code, {"error": "no"})])
        monkeypatch.setattr(delivery, "_post_once", caller)
        out = delivery.deliver(SITE, "k", _payload())
        assert not out["ok"] and caller.calls == 1 and out["reason"] == f"http_{code}"

    def test_missing_key_does_not_call(self, monkeypatch):
        caller = _Caller([(200, {})])
        monkeypatch.setattr(delivery, "_post_once", caller)
        out = delivery.deliver(SITE, "", _payload())
        assert not out["ok"] and out["reason"] == "no_key" and caller.calls == 0

    def test_missing_url_does_not_call(self, monkeypatch):
        caller = _Caller([(200, {})])
        monkeypatch.setattr(delivery, "_post_once", caller)
        out = delivery.deliver(dict(SITE, ingest_url=""), "k", _payload())
        assert not out["ok"] and out["reason"] == "no_ingest_url" and caller.calls == 0


# ───────── обратная связь по рубрикам ─────────


class TestUnknownSections:
    def test_reports_unknown_slugs(self):
        site = dict(SITE, sections=("novosti", "afisha"))
        assert delivery.unknown_sections(site, ["novosti", "sport", "sport", "eda"]) == [
            "sport",
            "eda",
        ]

    def test_known_only_yields_empty(self):
        site = dict(SITE, sections=("novosti",))
        assert delivery.unknown_sections(site, ["novosti", "NOVOSTI "]) == []

    def test_site_without_section_list_reports_nothing(self):
        assert delivery.unknown_sections(dict(SITE, sections=()), ["что-угодно"]) == []
