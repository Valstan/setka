"""Тесты VKDialogsChecker.parse_dialog_item (блок A): нормализация входящих ЛС."""

from __future__ import annotations

from modules.notifications.vk_dialogs_checker import VKDialogsChecker

_PROFILES = {42: {"id": 42, "first_name": "Иван", "last_name": "Петров"}}
_GROUPS = {200: {"id": 200, "name": "Рога и Копыта"}}


def _item(out=0, peer_type="user", peer_id=42, from_id=42, text="размещу рекламу", atts=None):
    return {
        "conversation": {
            "peer": {"id": peer_id, "type": peer_type},
            "last_message_id": 999,
        },
        "last_message": {
            "id": 999,
            "out": out,
            "from_id": from_id,
            "text": text,
            "attachments": atts or [],
            "date": 1700000000,
        },
    }


def test_parses_inbound_user_dialog():
    dlg = VKDialogsChecker.parse_dialog_item(_item(), _PROFILES, _GROUPS, -100)
    assert dlg is not None
    assert dlg["community_vk_id"] == -100
    assert dlg["peer_id"] == 42
    assert dlg["author_vk_id"] == 42
    assert dlg["author_is_group"] is False
    assert dlg["author_name"] == "Иван Петров"
    assert dlg["vk_post_id"] is None
    assert dlg["last_message_id"] == 999
    assert dlg["text"] == "размещу рекламу"


def test_skips_outgoing_last_message():
    # out=1 → мы ответили последними, заявку не заводим.
    assert VKDialogsChecker.parse_dialog_item(_item(out=1), _PROFILES, _GROUPS, -100) is None


def test_skips_non_user_peer():
    # Групповой чат (peer.type='chat') не обрабатываем.
    assert (
        VKDialogsChecker.parse_dialog_item(
            _item(peer_type="chat", peer_id=2000000001), _PROFILES, _GROUPS, -100
        )
        is None
    )


def test_skips_empty_message_without_attachments():
    assert VKDialogsChecker.parse_dialog_item(_item(text="   "), _PROFILES, _GROUPS, -100) is None


def test_keeps_empty_text_if_attachment_present():
    atts = [{"type": "photo", "photo": {"sizes": [{"width": 100, "url": "http://x/p.jpg"}]}}]
    dlg = VKDialogsChecker.parse_dialog_item(_item(text="", atts=atts), _PROFILES, _GROUPS, -100)
    assert dlg is not None
    assert dlg["photo_urls"] == ["http://x/p.jpg"]


def test_group_author_resolves_name_and_flag():
    # Сообщение от имени группы (from_id<0): но peer всё ещё может быть user-диалогом?
    # На практике from_id<0 в user-peer не встречается, но проверяем флаг и имя.
    dlg = VKDialogsChecker.parse_dialog_item(_item(from_id=-200), _PROFILES, _GROUPS, -100)
    assert dlg is not None
    assert dlg["author_is_group"] is True
    # имя берётся из profiles по peer_id (диалог с user 42), иначе из groups
    assert dlg["author_name"] == "Иван Петров"
