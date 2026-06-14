"""Probe VK cover-API (обложки сообществ) — директива brain 2026-06-14.

Probe-before-build (#020): до постройки шаблон-сборщика обложек выясняем главный
неизвестный — **права установки cover'а** (G19 ролевой барьер) и **охват** (на
скольких пабликах сети можем ставить обложку).

ЧИСТО ЧИТАЮЩИЙ / НЕ-DESTRUCTIVE:
- ``groups.getById(fields=cover)`` — текущая обложка (есть/нет + URL) [read];
- ``photos.getOwnerCoverPhotoUploadServer(group_id)`` — **только** проверка прав:
  метод возвращает адрес upload-сервера и НИЧЕГО не меняет (cover меняется лишь
  после upload + ``saveOwnerCoverPhoto``, которые мы НЕ вызываем). Успех = право
  есть; ошибка (15/27/…) = права нет.

Пробуем user-токен (владелец-админ), при отказе — community-токен группы.
Запуск на проде: sudo + source /etc/setka/setka.env (root-only env), затем
``./venv/bin/python scripts/probe_cover_api.py``. Вывод — JSON-сводка в stdout.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

# Канонический размер дисплея обложки VK (рекомендуемая загрузка — 2560×1440).
CROP = dict(crop_x=0, crop_y=0, crop_x2=1590, crop_y2=400)


def _err_text(e: Exception) -> str:
    code = getattr(e, "code", None)
    return f"[{code}] {e}" if code is not None else str(e)


def _cover_url(cover: Dict[str, Any]) -> Optional[str]:
    images = cover.get("images") or []
    if not images:
        return None
    return max(images, key=lambda i: int(i.get("width", 0))).get("url")


def _groups_list(raw: Any) -> List[Dict[str, Any]]:
    # vk_api может вернуть list (старые версии API) или {"groups": [...]}.
    if isinstance(raw, dict):
        return raw.get("groups", []) or []
    return raw or []


async def main() -> None:
    from modules.broadcast.service import default_targets
    from modules.vk_token_router import load_vk_routing

    user_token, community_tokens = await load_vk_routing()
    if not user_token:
        print(json.dumps({"error": "no user token (COMMUNITY_WRITE)"}, ensure_ascii=False))
        return

    from database.connection import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        targets = await default_targets(session)

    import vk_api

    user_api = vk_api.VkApi(token=user_token).get_api()
    gids = [abs(int(t["group_id"])) for t in targets]

    cover_by_gid: Dict[int, Dict[str, Any]] = {}
    try:
        raw = user_api.groups.getById(group_ids=",".join(map(str, gids)), fields="cover")
        for g in _groups_list(raw):
            cover_by_gid[int(g["id"])] = g
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": f"groups.getById failed: {_err_text(e)}"}, ensure_ascii=False))

    results: List[Dict[str, Any]] = []
    for t in targets:
        gid = abs(int(t["group_id"]))
        g = cover_by_gid.get(gid, {})
        cover = g.get("cover") or {}
        row: Dict[str, Any] = {
            "name": t["name"],
            "gid": gid,
            "has_cover": cover.get("enabled") == 1,
            "cover_url": _cover_url(cover),
            "can_set": False,
            "via": None,
            "error": None,
        }
        # 1) пробуем user-токеном (владелец-админ)
        try:
            user_api.photos.getOwnerCoverPhotoUploadServer(group_id=gid, **CROP)
            row["can_set"], row["via"] = True, "user"
        except Exception as e:  # noqa: BLE001
            user_err = _err_text(e)
            ct = community_tokens.get(gid)
            if ct:
                try:
                    vk_api.VkApi(token=ct).get_api().photos.getOwnerCoverPhotoUploadServer(
                        group_id=gid, **CROP
                    )
                    row["can_set"], row["via"] = True, "community"
                except Exception as e2:  # noqa: BLE001
                    row["error"] = f"user:{user_err}; community:{_err_text(e2)}"
            else:
                row["error"] = f"user:{user_err}; community:(no token)"
        results.append(row)
        time.sleep(0.34)  # бережём rate-limit

    summary = {
        "total": len(results),
        "can_set": sum(1 for r in results if r["can_set"]),
        "with_cover": sum(1 for r in results if r["has_cover"]),
        "rows": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
