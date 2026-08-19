"""Tests fail-safe рекордера аудита сбора (ADR-0004, вариант B).

Проверяем чистую ``build_audit_records`` (kept + content-дропы, механические
пропущены, дедуп по lip) и гейтинг ``should_audit`` (флаг + allowlist).
"""

from __future__ import annotations

from types import SimpleNamespace

from modules.curation import collection_audit as ca


def _post(pid, text, *, owner=-100, media=False):
    return {
        "owner_id": owner,
        "id": pid,
        "text": text,
        "attachments": [{"type": "photo"}] if media else [],
    }


def test_build_audit_records_classifies_both_sides():
    region_config = SimpleNamespace(delete_msg_blacklist=["спам-слово"])
    p_kept = _post(1, "матч состоялся", media=True)
    p_ad = _post(2, "Продам мяч, 500 руб, тел 89001112233")
    p_black = _post(3, "спам-слово рекламное", media=True)
    p_noatt = _post(4, "репортаж без фото")
    p_mech = _post(5, "просто матч с фото", media=True)  # не ad/blacklist, есть медиа → None
    p_dup = _post(2, "Продам мяч, 500 руб, тел 89001112233")  # дубль lip p_ad

    records = ca.build_audit_records(
        region_code="mi",
        theme="sport",
        region_config=region_config,
        collected=[p_kept, p_ad, p_black, p_noatt, p_mech, p_dup],
        kept=[p_kept],
    )
    by_lip = {r["lip"]: r for r in records}

    # механический дроп (p_mech) и дубль (p_dup) — не записаны
    assert len(records) == 4
    assert by_lip["100_1"]["decision"] == "kept"
    assert by_lip["100_1"]["drop_reason"] is None
    assert by_lip["100_2"]["decision"] == "dropped"
    assert by_lip["100_2"]["drop_reason"] == "advertisement"
    assert by_lip["100_3"]["drop_reason"] == "blacklist_text"
    assert by_lip["100_4"]["drop_reason"] == "no_attachments"
    assert "100_5" not in by_lip  # механический дроп пропущен


def test_build_audit_records_hard_spam_dropped_even_for_reklama():
    # Жёсткий спам/скам режется для ВСЕХ тем, включая reklama (доска объявлений):
    # is_advertisement там пропускается, а hard_spam — нет. Легальное частное
    # объявление в той же рубрике остаётся (kept).
    region_config = SimpleNamespace(delete_msg_blacklist=None)
    p_scam = _post(11, "Удалённая работа, рассылка рекламы, по готовой системе")
    p_legit = _post(12, "Продам велосипед, 3500 р", media=True)

    records = ca.build_audit_records(
        region_code="mi",
        theme="reklama",
        region_config=region_config,
        collected=[p_scam, p_legit],
        kept=[p_legit],
    )
    by_lip = {r["lip"]: r for r in records}

    assert by_lip["100_11"]["decision"] == "dropped"
    assert by_lip["100_11"]["drop_reason"] == "hard_spam"
    assert by_lip["100_12"]["decision"] == "kept"


def test_build_audit_records_snapshot_fields():
    records = ca.build_audit_records(
        region_code="mi",
        theme="novost",
        region_config=SimpleNamespace(delete_msg_blacklist=None),
        collected=[_post(7, "текст района", media=True)],
        kept=[_post(7, "текст района", media=True)],
    )
    assert len(records) == 1
    r = records[0]
    assert r["lip"] == "100_7"
    assert r["region_code"] == "mi"
    assert r["theme"] == "novost"
    assert r["post_url"] == "https://vk.com/wall-100_7"
    assert r["has_media"] is True


def test_should_audit_gating(monkeypatch):
    # OFF по умолчанию
    monkeypatch.delenv("COLLECTION_AUDIT_SHADOW_ENABLED", raising=False)
    assert ca.should_audit("mi") is False

    # ON без allowlist → все регионы
    monkeypatch.setenv("COLLECTION_AUDIT_SHADOW_ENABLED", "1")
    monkeypatch.delenv("COLLECTION_AUDIT_REGION_CODES", raising=False)
    assert ca.should_audit("mi") is True
    assert ca.should_audit("vp") is True

    # ON + allowlist mi → только mi
    monkeypatch.setenv("COLLECTION_AUDIT_REGION_CODES", "mi")
    assert ca.should_audit("mi") is True
    assert ca.should_audit("vp") is False


def test_snapshot_records_vk_post_date():
    """published_at берётся из поля date поста ВК (unix) и хранится наивным UTC.

    Дата нужна для отсева по старости: 72 часа считаются как ВОЗРАСТ ПОСТА,
    а не как время, прошедшее с нашего сбора. Собрать пост можно сильно позже
    его публикации, и тогда collected_at соврал бы в нашу пользу.
    """
    from datetime import datetime

    from modules.curation.collection_audit import build_audit_records

    post = {"owner_id": -100, "id": 7, "text": "текст", "date": 1787136000}
    records = build_audit_records(
        region_code="mi",
        theme="novost",
        region_config=None,
        collected=[post],
        kept=[post],
    )
    assert len(records) == 1
    assert records[0]["published_at"] == datetime(2026, 8, 19, 10, 40)


def test_snapshot_without_date_leaves_published_at_none():
    """Нет поля date → None, а не «сейчас»: подставленная дата обманула бы отсев."""
    from modules.curation.collection_audit import build_audit_records

    post = {"owner_id": -100, "id": 8, "text": "текст"}
    records = build_audit_records(
        region_code="mi",
        theme="novost",
        region_config=None,
        collected=[post],
        kept=[post],
    )
    assert records[0]["published_at"] is None


def test_snapshot_with_broken_date_leaves_published_at_none():
    """Битое значение date не роняет аудит — он никогда не валит сбор."""
    from modules.curation.collection_audit import build_audit_records

    for bad in ("не число", None, [], 10**20):
        post = {"owner_id": -100, "id": 9, "text": "текст", "date": bad}
        records = build_audit_records(
            region_code="mi",
            theme="novost",
            region_config=None,
            collected=[post],
            kept=[post],
        )
        assert records[0]["published_at"] is None, f"date={bad!r}"
