"""VK-вызовы канала автооформления (setup): описание, аватар, обложка, закреп.

Раскладка «операция × токен» снята живой пробой на тест-полигоне
(``docs/ops/group-setup-probe.md``, 2026-08-29) и НЕ совпадает с интуицией:

- описание (``groups.edit``) и обложка (полный цикл ``getOwnerCoverPhoto*``) —
  **community-ключ** сообщества;
- аватар (``photos.getOwnerPhotoUploadServer`` → ``saveOwnerPhoto``) и весь
  ``wall.pin``/``wall.delete`` — **только user-токен владельца** (VALSTAN);
- ``saveOwnerPhoto`` публикует системный пост (пустой текст + фото) — его
  обязан удалять ``upload_avatar`` сам, иначе 25 голых постов по сети;
- город и статус через API **не правятся вообще**: ``groups.edit`` возвращает
  ok=1 и молча игнорирует поля (ловушка класса #219, проверено трижды).
  Поэтому здесь их нет — город/статус ставятся в веб-интерфейсе.

Все функции синхронные (vk_api), зовутся из CLI ``scripts/setup_groups.py``
через ``asyncio.to_thread`` при нужде. Ошибки не поднимаются наружу — каждый
вызов возвращает :class:`SetupResult`, решение «продолжать или стоп» принимает
оркестратор (стоп-сигналы ВК — ``modules/promotion/vk_errors.py``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

_UPLOAD_TIMEOUT = 60

# Канон кропа обложки (см. docs/ops/group-setup-probe.md и probe_cover_api.py).
COVER_CROP = dict(crop_x=0, crop_y=0, crop_x2=1590, crop_y2=400)


@dataclass
class SetupResult:
    """Итог одного VK-действия: успех, код ошибки ВК (если была), детали."""

    ok: bool
    vk_error_code: Optional[int] = None
    detail: str = ""
    payload: Optional[Dict[str, Any]] = None


def _vk_error(exc: Exception) -> SetupResult:
    code = getattr(exc, "code", None)
    return SetupResult(ok=False, vk_error_code=code, detail=str(exc)[:200])


def get_current(api, group_id: int) -> SetupResult:
    """Снимок полей сообщества до правки — основа ``before`` в promo_group_setup."""
    gid = abs(int(group_id))
    try:
        rows = api.groups.getById(
            group_id=gid, fields="description,status,city,has_photo,cover,screen_name"
        )
        if isinstance(rows, dict):
            rows = rows.get("groups", rows)
        row = rows[0] if rows else {}
        return SetupResult(
            ok=True,
            payload={
                "description": row.get("description") or "",
                "status": row.get("status") or "",
                "city": (row.get("city") or {}).get("title"),
                "city_id": (row.get("city") or {}).get("id"),
                "has_photo": row.get("has_photo"),
                "has_cover": bool((row.get("cover") or {}).get("enabled")),
                "screen_name": row.get("screen_name"),
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _vk_error(exc)


def edit_description(community_api, group_id: int, description: str) -> SetupResult:
    """``groups.edit(description=…)`` community-ключом (проба: работает)."""
    try:
        community_api.groups.edit(group_id=abs(int(group_id)), description=description)
        return SetupResult(ok=True)
    except Exception as exc:  # noqa: BLE001
        return _vk_error(exc)


def upload_avatar(user_api, group_id: int, image_bytes: bytes) -> SetupResult:
    """Аватар user-токеном владельца: get server → multipart → save → чистка.

    После ``saveOwnerPhoto`` ВК публикует системный пост с фото — находим его
    на верху стены (пустой текст, один photo-attachment) и удаляем тем же
    user-токеном. Промах чистки — не провал аватара: вернём ok с пометкой.
    """
    gid = abs(int(group_id))
    try:
        server = user_api.photos.getOwnerPhotoUploadServer(owner_id=-gid)
        upload_url = server["upload_url"]
        up = requests.post(
            upload_url,
            files={"photo": ("avatar.jpg", image_bytes, "image/jpeg")},
            timeout=_UPLOAD_TIMEOUT,
        ).json()
        if not up.get("photo"):
            return SetupResult(ok=False, detail=f"upload вернул пусто: {str(up)[:150]}")
        user_api.photos.saveOwnerPhoto(
            server=up.get("server"), hash=up.get("hash"), photo=up.get("photo")
        )
    except Exception as exc:  # noqa: BLE001
        return _vk_error(exc)

    # Чистка системного поста — отдельным try: аватар уже стоит.
    try:
        wall = user_api.wall.get(owner_id=-gid, count=3)
        for item in wall.get("items", []):
            atts = item.get("attachments") or []
            if (
                not (item.get("text") or "").strip()
                and len(atts) == 1
                and atts[0].get("type") == "photo"
            ):
                user_api.wall.delete(owner_id=-gid, post_id=item["id"])
                return SetupResult(ok=True, detail="системный пост удалён")
        return SetupResult(ok=True, detail="системный пост не найден (ок)")
    except Exception as exc:  # noqa: BLE001
        logger.warning("avatar set, system-post cleanup failed for %s: %s", gid, exc)
        return SetupResult(ok=True, detail=f"аватар стоит, чистка не удалась: {str(exc)[:120]}")


def upload_cover(community_api, group_id: int, image_bytes: bytes) -> SetupResult:
    """Обложка community-ключом: get server (с кропом) → multipart → save."""
    gid = abs(int(group_id))
    try:
        server = community_api.photos.getOwnerCoverPhotoUploadServer(group_id=gid, **COVER_CROP)
        upload_url = server["upload_url"]
        up = requests.post(
            upload_url,
            files={"photo": ("cover.jpg", image_bytes, "image/jpeg")},
            timeout=_UPLOAD_TIMEOUT,
        ).json()
        if "hash" not in up:
            return SetupResult(ok=False, detail=f"upload вернул пусто: {str(up)[:150]}")
        community_api.photos.saveOwnerCoverPhoto(hash=up.get("hash"), photo=up.get("photo"))
        return SetupResult(ok=True)
    except Exception as exc:  # noqa: BLE001
        return _vk_error(exc)


def post_welcome(community_api, group_id: int, text: str) -> SetupResult:
    """Пост-визитка от имени сообщества (community-ключ). Возвращает post_id."""
    gid = abs(int(group_id))
    try:
        resp = community_api.wall.post(owner_id=-gid, from_group=1, message=text)
        post_id = (resp or {}).get("post_id")
        if not post_id:
            return SetupResult(ok=False, detail=f"wall.post без post_id: {str(resp)[:100]}")
        return SetupResult(ok=True, payload={"post_id": int(post_id)})
    except Exception as exc:  # noqa: BLE001
        return _vk_error(exc)


def pin_post(user_api, group_id: int, post_id: int) -> SetupResult:
    """Закрепить пост (только user-токен: community → error 27, проба)."""
    try:
        user_api.wall.pin(owner_id=-abs(int(group_id)), post_id=int(post_id))
        return SetupResult(ok=True)
    except Exception as exc:  # noqa: BLE001
        return _vk_error(exc)
