"""Тесты конфигурации сетевого хаба copy/setka (без VK/БД)."""


def test_copy_setka_source_group_id_parses(monkeypatch):
    monkeypatch.setenv("COPY_SETKA_SOURCE_GROUP_ID", "-123456")
    from config.runtime import get_copy_setka_source_owner_id

    assert get_copy_setka_source_owner_id() == -123456


def test_copy_setka_default_source_is_copy_by_setka(monkeypatch):
    """Без env — группа vk.com/copy_by_setka (-167381590)."""
    monkeypatch.delenv("COPY_SETKA_SOURCE_GROUP_ID", raising=False)
    from config.runtime import get_copy_setka_source_owner_id

    assert get_copy_setka_source_owner_id() == -167381590


def test_copy_setka_use_repost_default(monkeypatch):
    monkeypatch.delenv("COPY_SETKA_USE_REPOST", raising=False)
    from config.runtime import copy_setka_use_repost

    assert copy_setka_use_repost() is True


def test_copy_setka_target_codes(monkeypatch):
    monkeypatch.setenv("COPY_SETKA_TARGET_REGION_CODES", " Ur , pizhanka ")
    from config.runtime import get_copy_setka_target_region_codes

    assert get_copy_setka_target_region_codes() == {"ur", "pizhanka"}


def test_copy_setka_disabled(monkeypatch):
    monkeypatch.setenv("COPY_SETKA_DISABLED", "1")
    from config.runtime import copy_setka_disabled

    assert copy_setka_disabled() is True
