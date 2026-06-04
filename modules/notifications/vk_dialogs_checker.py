"""VK Inbound Dialogs Checker (рекламный кабинет, блок A).

Читает входящие диалоги главной группы региона через ``messages.getConversations``
и нормализует **последнее входящее сообщение** каждого диалога в тот же формат,
что ``VKSuggestedChecker.parse_suggested_item`` отдаёт для предложки. Это позволяет
переиспользовать ``classifier.classify`` и ``ad_cabinet.scanner`` практически без
изменений — отличается только источник (origin='inbound_dm').

Особенность токенов (как у ``VKMessagesChecker``): community-токену параметр
``group_id`` НЕ нужен (подразумевается), user-токену — нужен явно. Поэтому
используем ``_api_for`` напрямую, а не ``_call_with_fallback`` (у того единая
сигнатура ``fn(api)`` без знания типа токена).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from vk_api.exceptions import ApiError

from modules.notifications.base_checker import BaseVKChecker

logger = logging.getLogger(__name__)


def _extract_photo_urls(attachments) -> List[str]:
    """Ссылки на крупнейшую версию каждого фото-вложения (показ в карточке)."""
    urls: List[str] = []
    for att in attachments or []:
        if att.get("type") != "photo":
            continue
        sizes = (att.get("photo") or {}).get("sizes") or []
        if not sizes:
            continue
        best = max(sizes, key=lambda s: s.get("width", 0) or 0)
        if best.get("url"):
            urls.append(best["url"])
    return urls


class VKDialogsChecker(BaseVKChecker):
    """Входящие диалоги сообщества → нормализованные «посты» для классификатора."""

    CHECKER_NAME = "VK Dialogs Checker"

    def fetch_inbound_dialogs(self, group_id: int, count: int = 100) -> List[Dict[str, Any]]:
        """Нормализованные входящие диалоги группы (последнее сообщение каждого).

        Берём только диалоги, где **последнее сообщение входящее** (``out=0``) и
        собеседник — пользователь (``peer.type='user'``): это значит, что мы ещё
        не ответили / автор написал последним. На уже отвеченные (``out=1``) и
        групповые чаты не реагируем. Не бросает наружу — при ошибке VK отдаёт
        ``[]`` (скан не должен падать на одной группе).
        """
        positive_id = abs(int(group_id))
        api, via_community = self._api_for(group_id)
        try:
            if via_community:
                result = api.messages.getConversations(count=count, extended=1)
            else:
                result = api.messages.getConversations(
                    group_id=positive_id, count=count, extended=1
                )
        except ApiError as e:
            logger.warning("fetch_inbound_dialogs group %s: %s (code %s)", group_id, e, e.code)
            return []
        except Exception as e:  # pragma: no cover - сетевые флапы
            logger.error("fetch_inbound_dialogs group %s: %s", group_id, e)
            return []

        items = result.get("items", []) or []
        profiles = {p["id"]: p for p in (result.get("profiles") or [])}
        groups = {g["id"]: g for g in (result.get("groups") or [])}

        parsed: List[Dict[str, Any]] = []
        for item in items:
            try:
                dlg = self.parse_dialog_item(item, profiles, groups, group_id)
                if dlg is not None:
                    parsed.append(dlg)
            except Exception as e:
                logger.warning("parse dialog item failed (group %s): %s", group_id, e)

        logger.info(
            "Group %s: fetched %d inbound dialogs (via %s)",
            group_id,
            len(parsed),
            "community-token" if via_community else "user-token",
        )
        return parsed

    @staticmethod
    def parse_dialog_item(
        item: Dict[str, Any],
        profiles: Dict[int, Dict[str, Any]],
        groups: Dict[int, Dict[str, Any]],
        owner_id: int,
    ) -> Dict[str, Any] | None:
        """Нормализовать один диалог. ``None`` — диалог не подлежит обработке.

        Возвращает dict с теми же ключами, что ``parse_suggested_item`` (плюс
        ``last_message_id``), чтобы scanner/classifier работали без ветвлений.
        Пропускаем (``None``): исходящие (мы ответили последними), не-user peer
        (групповые чаты), пустой текст без вложений.
        """
        conv = item.get("conversation") or {}
        last = item.get("last_message") or {}
        peer = conv.get("peer") or {}

        peer_id = peer.get("id")
        peer_type = peer.get("type")
        out = int(last.get("out", 0) or 0)
        from_id = last.get("from_id")

        # Только входящие диалоги с пользователем, на которые ещё не ответили.
        if out == 1 or peer_type != "user" or not peer_id or int(peer_id) <= 0:
            return None

        text = last.get("text", "") or ""
        attachments = last.get("attachments", []) or []
        if not text.strip() and not attachments:
            return None

        author_is_group = from_id is not None and int(from_id) < 0
        author_vk_id = int(from_id) if from_id is not None else int(peer_id)

        author_name = None
        prof = profiles.get(int(peer_id))
        if prof:
            author_name = (
                " ".join(x for x in [prof.get("first_name"), prof.get("last_name")] if x).strip()
                or None
            )
        elif author_is_group:
            grp = groups.get(abs(int(from_id)))
            if grp:
                author_name = grp.get("name")

        return {
            # post-совместимые поля (для classify + scanner)
            "vk_post_id": None,
            "community_vk_id": owner_id,
            "from_id": from_id,
            "signer_id": None,
            "author_vk_id": author_vk_id,
            "peer_id": int(peer_id),
            "author_is_group": author_is_group,
            "author_name": author_name,
            "text": text,
            "marked_as_ads": False,
            "attachments": attachments,
            "photo_urls": _extract_photo_urls(attachments),
            "date": last.get("date"),
            # специфичное для ЛС
            "last_message_id": conv.get("last_message_id") or last.get("id"),
        }
