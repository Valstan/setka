"""Загрузка картинок в ЛС VK (офферные изображения рекламного кабинета).

Единственная реально новая VK-интеграция MVP. ``messages.send`` принимает
только VK-attachment'ы (``photo<owner>_<id>``), поэтому офферные картинки надо
один раз залить через messages-upload-server и закэшировать attachment-строку.

⚠️ Грузить нужно **community-токеном отправляющей группы** (R4) — иначе владелец
фото будет другой, и VK неправильно прикрепит / отклонит. По одной картинке за
вызов upload-сервера; ЛС VK принимает максимум 5 вложений.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

MAX_MESSAGE_PHOTOS = 5
_UPLOAD_TIMEOUT = 30


def upload_message_photo(
    api,
    image_bytes: bytes,
    *,
    peer_id: Optional[int] = None,
    filename: str = "offer.jpg",
) -> Optional[str]:
    """Залить одну картинку в messages-контекст.

    Returns ``"photo<owner>_<id>"`` (с ``_access_key`` если есть) или ``None``.
    """
    try:
        if peer_id:
            server = api.photos.getMessagesUploadServer(peer_id=int(peer_id))
        else:
            server = api.photos.getMessagesUploadServer()
        upload_url = server["upload_url"]

        # Сырой multipart POST (vk_api.method() это не умеет). Поле строго "photo".
        up = requests.post(
            upload_url,
            files={"photo": (filename, image_bytes, "image/jpeg")},
            timeout=_UPLOAD_TIMEOUT,
        ).json()
        if not up.get("photo") or up.get("photo") in ("[]", ""):
            logger.warning("messages upload returned empty photo: %s", up)
            return None

        saved = api.photos.saveMessagesPhoto(
            photo=up["photo"], server=up["server"], hash=up["hash"]
        )
        if not saved:
            return None
        p = saved[0]
        att = f"photo{p['owner_id']}_{p['id']}"
        if p.get("access_key"):
            att += f"_{p['access_key']}"
        return att
    except Exception as e:
        logger.warning("upload_message_photo failed: %s", e)
        return None


def upload_offer_images(api, images: List[bytes], *, peer_id: Optional[int] = None) -> str:
    """Залить до 5 картинок, вернуть attachment-строку ``"photo..,photo.."``.

    Частичный успех допустим: что залилось, то и прикрепим.
    """
    parts: List[str] = []
    for i, blob in enumerate(images[:MAX_MESSAGE_PHOTOS]):
        att = upload_message_photo(api, blob, peer_id=peer_id, filename=f"offer_{i}.jpg")
        if att:
            parts.append(att)
    return ",".join(parts)
